#pragma once

#include <stddef.h>
#include <stdint.h>

namespace observer {

class QueueSequenceState {
 public:
  uint32_t next() const { return next_; }
  void advance(uint32_t assigned) {
    next_ = assigned + 1;
  }
  void restart() { next_ = 1; }
  void skip() { if (next_ != 0) advance(next_); }
 private:
  uint32_t next_ = 1;
};

struct RecordFragment {
  uint64_t boot_nonce;
  uint32_t sequence;
  uint16_t total_length;
  uint16_t offset;
  const uint8_t* bytes;
  uint16_t length;
};

template <size_t ByteCapacity, size_t RecordCapacity>
class RecordQueue {
 public:
  explicit RecordQueue(bool enabled = true, uint64_t boot_nonce = 0, uint32_t next_sequence = 1)
      : enabled_(enabled), boot_nonce_(boot_nonce), next_sequence_(next_sequence ? next_sequence : 1) {}
  RecordQueue(bool enabled, uint64_t boot_nonce, QueueSequenceState& sequence)
      : enabled_(enabled), boot_nonce_(boot_nonce), next_sequence_(sequence.next()), sequence_(&sequence) {}

  bool enqueue(const uint8_t* bytes, size_t length) {
    if (!enabled_) return true;
    if (!bytes || length == 0 || length > kMaxRecordLength) {
      drop(length);
      return false;
    }
    // Zero means exhausted, not reusable sequence 1. Only the loop-side nonce
    // rotation can reopen admission; do not evict backlog while entropy waits.
    if (sequenceExhausted()) {
      rejectUnavailable(length);
      return false;
    }
    while (length > availableBytes() || record_count_ == RecordCapacity) {
      if (record_count_ == 0) {
        drop(length);
        return false;
      }
      dropHeadRecord();
    }
    if (length > availableBytes() || record_count_ == RecordCapacity) {
      drop(length);
      return false;
    }
    if (byte_count_ == 0) byte_tail_ = byte_head_;
    const bool was_empty = record_count_ == 0;
    Record& record = records_[record_tail_];
    record.boot_nonce = boot_nonce_;
    record.sequence = next_sequence_++;
    if (sequence_) sequence_->advance(record.sequence);
    record.start = byte_tail_;
    record.length = length;
    record.consumed = 0;
    record.transmit_offset = 0;
    for (size_t index = 0; index < length; ++index) {
      bytes_[byte_tail_] = bytes[index];
      byte_tail_ = (byte_tail_ + 1) % ByteCapacity;
    }
    byte_count_ += length;
    if (was_empty) byte_head_ = record.start;
    record_tail_ = (record_tail_ + 1) % RecordCapacity;
    ++record_count_;
    return true;
  }

  bool sequenceExhausted() const { return next_sequence_ == 0; }
  bool setBootNonce(uint64_t boot_nonce) {
    if (!sequenceExhausted() || boot_nonce == 0 || boot_nonce == boot_nonce_) return false;
    boot_nonce_ = boot_nonce;
    next_sequence_ = 1;
    if (sequence_) sequence_->restart();
    return true;
  }

  bool peek(RecordFragment& fragment, size_t maximum) const {
    if (record_count_ == 0) return false;
    const Record& record = records_[record_head_];
    fragment.boot_nonce = record.boot_nonce;
    fragment.sequence = record.sequence;
    fragment.total_length = record.length;
    fragment.offset = static_cast<uint16_t>(record.transmit_offset);
    fragment.bytes = &bytes_[(record.start + record.transmit_offset) % ByteCapacity];
    const size_t contiguous = ByteCapacity - ((record.start + record.transmit_offset) % ByteCapacity);
    const size_t remaining = record.length - record.transmit_offset;
    fragment.length = remaining < maximum ? remaining : maximum;
    if (fragment.length > contiguous) fragment.length = contiguous;
    return true;
  }

  bool consume(uint64_t boot_nonce, uint32_t sequence, size_t length) {
    if (record_count_ == 0 || boot_nonce != records_[record_head_].boot_nonce || records_[record_head_].sequence != sequence) return false;
    Record& record = records_[record_head_];
    if (length > record.length - record.transmit_offset) return false;
    record.transmit_offset += length;  // progress only; storage stays until complete Pi ACK
    return true;
  }

  void consume(size_t length) {
    if (record_count_ == 0) return;
    consume(records_[record_head_].boot_nonce, records_[record_head_].sequence, length);
  }

  void dropHeadRecord() {
    if (record_count_ == 0) return;
    Record& record = records_[record_head_];
    const size_t remaining = record.length;
    ++dropped_records_;
    dropped_bytes_ += remaining;
    ++evicted_records_;
    evicted_bytes_ += remaining;
    byte_count_ -= remaining;
    byte_head_ = (byte_head_ + remaining) % ByteCapacity;
    if (record_count_ > 1) byte_head_ = records_[(record_head_ + 1) % RecordCapacity].start;
    removeHead();
  }

  void drop(size_t length) {
    if (!enabled_) return;
    reserveLostIdentity();
    ++dropped_records_;
    dropped_bytes_ += length;
    ++truncated_records_;
    truncated_bytes_ += length;
  }

  void rejectUnavailable(size_t length) {
    if (!enabled_) return;
    reserveLostIdentity();
    ++dropped_records_;
    dropped_bytes_ += length;
    ++unavailable_records_;
    unavailable_bytes_ += length;
  }

  bool empty() const { return record_count_ == 0; }
  void setEnabled(bool enabled) { enabled_ = enabled; }
  size_t size() const { return record_count_; }
  uint32_t headSequence() const { return record_count_ ? records_[record_head_].sequence : 0; }

  bool acknowledge(uint64_t boot_nonce, uint32_t sequence) {
    if (record_count_ == 0 || boot_nonce != records_[record_head_].boot_nonce || sequence == 0 ||
        records_[record_head_].sequence != sequence) return false;
    const size_t remaining = records_[record_head_].length;
    byte_count_ -= remaining;
    byte_head_ = (byte_head_ + remaining) % ByteCapacity;
    if (record_count_ > 1) byte_head_ = records_[(record_head_ + 1) % RecordCapacity].start;
    removeHead();
    return true;
  }

  void restartHead() {
    if (record_count_ != 0) records_[record_head_].transmit_offset = 0;
  }

  uint64_t bootNonce() const { return boot_nonce_; }

  uint32_t droppedRecords() const { return dropped_records_; }
  uint32_t droppedBytes() const { return dropped_bytes_; }
  uint32_t evictedRecords() const { return evicted_records_; }
  uint32_t evictedBytes() const { return evicted_bytes_; }
  uint32_t truncatedRecords() const { return truncated_records_; }
  uint32_t truncatedBytes() const { return truncated_bytes_; }
  uint32_t unavailableRecords() const { return unavailable_records_; }
  uint32_t unavailableBytes() const { return unavailable_bytes_; }

 private:
  void reserveLostIdentity() {
    // Missing classic records occupy sequence positions too. Never wrap zero
    // back to 1; a fresh loop-side nonce is required after exhaustion.
    if (sequenceExhausted()) return;
    const uint32_t lost = next_sequence_++;
    if (sequence_) sequence_->advance(lost);
  }

  struct Record {
    uint64_t boot_nonce;
    uint32_t sequence;
    size_t start;
    size_t length;
    size_t consumed;
    size_t transmit_offset;
  };

  void removeHead() {
    record_head_ = (record_head_ + 1) % RecordCapacity;
    --record_count_;
  }
  size_t availableBytes() const { return ByteCapacity - byte_count_; }


  uint8_t bytes_[ByteCapacity]{};
  Record records_[RecordCapacity]{};
  size_t byte_head_ = 0;
  size_t byte_tail_ = 0;
  size_t byte_count_ = 0;
  size_t record_head_ = 0;
  size_t record_tail_ = 0;
  size_t record_count_ = 0;
  static constexpr size_t kMaxRecordLength = 544;
  uint64_t boot_nonce_ = 0;
  uint32_t next_sequence_ = 1;
  QueueSequenceState* sequence_ = nullptr;
  bool enabled_;
  uint32_t dropped_records_ = 0;
  uint32_t dropped_bytes_ = 0;
  uint32_t evicted_records_ = 0;
  uint32_t evicted_bytes_ = 0;
  uint32_t truncated_records_ = 0;
  uint32_t truncated_bytes_ = 0;
  uint32_t unavailable_records_ = 0;
  uint32_t unavailable_bytes_ = 0;

  void recomputeByteCount() {
    size_t total = 0;
    for (size_t index = 0; index < record_count_; ++index) {
      const Record& record = records_[(record_head_ + index) % RecordCapacity];
      total += record.length;
    }
    byte_count_ = total;
  }
};

}  // namespace observer
