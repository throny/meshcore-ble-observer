#pragma once

#include "Protocol.h"

namespace observer {
struct RecordFragment;
namespace coc {

enum class Action : uint8_t { Idle, Retry, Close };

// Loop-owned state only. Stack callbacks must use a bounded synchronized event
// handoff; this class is not thread-safe and does not authorize a peer/channel.
// Keep the object alive until all vendor-owned buffers have been returned.
class Delivery {
 public:
  Delivery() = default;
  Delivery(const Delivery&) = delete;
  Delivery& operator=(const Delivery&) = delete;
  Delivery(Delivery&&) = delete;
  Delivery& operator=(Delivery&&) = delete;

  bool open(uint32_t generation) {
    if (!closed_ || owned_ || generation == 0 || generation <= generation_) return false;
    generation_ = generation;
    closed_ = false;
    acknowledged_ = false;
    attempts_ = 0;
    wait_ = Wait::None;
    submit_ready_ = pending();
    return true;
  }

  template <typename Queue, typename Fragment = RecordFragment>
  bool load(Queue& queue) {
    if (closed_ || pending() || owned_ || queue.empty()) return false;
    queue.restartHead();
    // This is a ring-storage copy, not BLE fragmentation or reassembly.
    uint64_t session = 0;
    uint32_t sequence = 0;
    size_t total = 0;
    size_t copied = 0;
    do {
      Fragment fragment;
      if (!queue.peek(fragment, kRecordBytes)) {
        queue.restartHead();
        return false;
      }
      if (copied == 0) {
        session = fragment.boot_nonce;
        sequence = fragment.sequence;
        total = fragment.total_length;
      }
      if (session == 0 || sequence == 0 || total == 0 || total > kRecordBytes ||
          fragment.boot_nonce != session || fragment.sequence != sequence ||
          fragment.total_length != total || fragment.offset != copied || !fragment.bytes ||
          fragment.length == 0 || fragment.length > total - copied) {
        queue.restartHead();
        return false;
      }
      memcpy(storage_ + kHeaderBytes + copied, fragment.bytes, fragment.length);
      if (!queue.consume(session, sequence, fragment.length)) {
        queue.restartHead();
        return false;
      }
      copied += fragment.length;
    } while (copied < total);
    if (!encode(Kind::Record, session, sequence, storage_ + kHeaderBytes,
                total, storage_, sizeof(storage_)) || !queue.acknowledge(session, sequence)) {
      queue.restartHead();
      return false;
    }
    // Local queue -> pending ownership transfer, NOT host receipt. Never later
    // acknowledge the queue head from a CoC receipt: it may already be different.
    session_ = session;
    sequence_ = sequence;
    size_ = static_cast<uint16_t>(total + kHeaderBytes);
    acknowledged_ = false;
    attempts_ = 0;
    submit_ready_ = true;
    wait_ = Wait::None;
    return true;
  }

  bool ready(uint32_t now) const {
    return !closed_ && pending() && !owned_ && !acknowledged_ &&
        submit_ready_ && attempts_ < 3 && !expired(now);
  }

  // Call only after ready(now) and a successful vendor submit, using the SAME
  // sampled now. On vendor resource failure call resourcesBusy instead. The
  // real adapter must serialize this transaction with callback event processing.
  bool submitAccepted(uint32_t generation, uint32_t now) {
    if (generation != generation_ || !ready(now)) return false;
    owned_ = true;
    submit_ready_ = false;
    ++attempts_;
    wait_ = Wait::Tx;
    wait_started_ = now;
    return true;
  }

  bool resourcesBusy(uint32_t generation, uint32_t now) {
    if (generation != generation_ || closed_ || !pending() || owned_ || acknowledged_ ||
        (wait_ != Wait::None && wait_ != Wait::Resources)) return false;
    if (wait_ != Wait::Resources) wait_started_ = now;
    wait_ = Wait::Resources;
    submit_ready_ = false;
    return true;
  }

  bool resourcesAvailable(uint32_t generation) {
    if (generation != generation_ || closed_ || wait_ != Wait::Resources) return false;
    submit_ready_ = true;  // Absolute stall deadline deliberately unchanged.
    return true;
  }

  bool returned(uint32_t generation, const uint8_t* pointer, uint32_t now) {
    if (generation != generation_ || !owned_ || pointer != storage_) return false;
    owned_ = false;
    if (acknowledged_) retire();
    else if (!closed_) {
      wait_ = Wait::Receipt;
      wait_started_ = now;
    }
    return true;
  }

  bool receipt(uint32_t generation, uint64_t session, uint32_t sequence) {
    if (closed_ || generation != generation_ || !pending() || attempts_ == 0 ||
        session != session_ || sequence != sequence_) return false;
    acknowledged_ = true;
    submit_ready_ = false;
    if (!owned_) retire();
    return true;
  }

  Action poll(uint32_t now) {
    if (closed_ || !expired(now)) return Action::Idle;
    if (wait_ != Wait::Receipt || attempts_ >= 3) {
      close();
      return Action::Close;
    }
    wait_ = Wait::None;
    submit_ready_ = true;
    return Action::Retry;
  }

  void close() {
    closed_ = true;
    submit_ready_ = false;
    wait_ = Wait::None;
    // Outstanding vendor ownership and unreceipted bytes survive channel loss.
  }

  bool wakeDelay(uint32_t now, uint32_t& delay) const {
    if (closed_ || !pending()) return false;
    if (ready(now) || expired(now)) { delay = 0; return true; }
    if (wait_ == Wait::None) return false;
    const uint32_t interval = wait_ == Wait::Receipt ? 5000 : 15000;
    delay = interval - static_cast<uint32_t>(now - wait_started_);
    return true;
  }

  bool pending() const { return size_ != 0; }
  bool vendorOwned() const { return owned_; }
  bool closed() const { return closed_; }
  uint8_t attempts() const { return attempts_; }
  uint64_t session() const { return session_; }
  uint32_t sequence() const { return sequence_; }
  size_t size() const { return size_; }
  const uint8_t* data() const { return pending() ? storage_ : nullptr; }

 private:
  enum class Wait : uint8_t { None, Resources, Tx, Receipt };

  bool expired(uint32_t now) const {
    if (wait_ == Wait::None) return false;
    const uint32_t interval = wait_ == Wait::Receipt ? 5000 : 15000;
    return static_cast<uint32_t>(now - wait_started_) >= interval;
  }

  void retire() {
    size_ = 0;
    acknowledged_ = false;
    submit_ready_ = false;
    wait_ = Wait::None;
  }

  uint8_t storage_[kSduBytes]{};
  uint64_t session_ = 0;
  uint32_t sequence_ = 0;
  uint32_t generation_ = 0;
  uint32_t wait_started_ = 0;
  uint16_t size_ = 0;
  uint8_t attempts_ = 0;
  Wait wait_ = Wait::None;
  bool owned_ = false;
  bool acknowledged_ = false;
  bool submit_ready_ = false;
  bool closed_ = true;
};

}  // namespace coc
}  // namespace observer
