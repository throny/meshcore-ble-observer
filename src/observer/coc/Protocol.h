#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace observer {
namespace coc {

// Local experimental observer contract, not a Bluetooth profile allocation.
constexpr size_t kHeaderBytes = 14;
constexpr size_t kRecordBytes = 544;
constexpr size_t kSduBytes = kHeaderBytes + kRecordBytes;

enum class Kind : uint8_t {
  Hello = 1, Ready, Record, Receipt, Command, Reply, StatsRequest, StatsReply
};
enum class Direction : uint8_t { ToDevice, ToHost };

struct Packet {
  Kind kind = Kind::Hello;
  uint64_t session = 0;
  uint32_t id = 0;
  const uint8_t* payload = nullptr;
  size_t length = 0;
};

inline bool toDevice(Kind kind) {
  return kind == Kind::Ready || kind == Kind::Receipt ||
      kind == Kind::Command || kind == Kind::StatsRequest;
}

inline bool validPayload(Kind kind, uint32_t id, const uint8_t* bytes, size_t length) {
  const uint8_t value = static_cast<uint8_t>(kind);
  if (value < 1 || value > 8 || (length != 0 && !bytes)) return false;
  if (kind == Kind::Hello || kind == Kind::Ready) {
    return id == 0 && length == 2 && bytes[0] == 0x20 && bytes[1] == 2;
  }
  if (id == 0) return false;
  if (kind == Kind::Receipt || kind == Kind::StatsRequest) return length == 0;
  if (kind == Kind::Command) {
    if (length == 0 || length > 95) return false;
    for (size_t i = 0; i < length; ++i) {
      if (bytes[i] == 0 || bytes[i] == '\r' || bytes[i] == '\n') return false;
    }
    return true;  // Existing command allowlist remains a separate admission gate.
  }
  const size_t maximum = kind == Kind::StatsReply ? 128 : kRecordBytes;
  return length != 0 && length <= maximum;
}

// Decode borrows the caller's complete SDU. It neither authenticates a peer nor
// authorizes a handshake/session/command. Callers must enforce those separately.
inline bool decode(const uint8_t* bytes, size_t length, Direction direction, Packet& packet) {
  packet = Packet();
  if (!bytes || length < kHeaderBytes || length > kSduBytes || bytes[0] != 1) return false;
  const Kind kind = static_cast<Kind>(bytes[1]);
  if (toDevice(kind) != (direction == Direction::ToDevice)) return false;
  uint64_t session = 0;
  uint32_t id = 0;
  for (unsigned i = 0; i < 8; ++i) session |= static_cast<uint64_t>(bytes[2 + i]) << (8 * i);
  for (unsigned i = 0; i < 4; ++i) id |= static_cast<uint32_t>(bytes[10 + i]) << (8 * i);
  if (session == 0 || !validPayload(kind, id, bytes + kHeaderBytes, length - kHeaderBytes)) return false;
  packet.kind = kind;
  packet.session = session;
  packet.id = id;
  packet.payload = bytes + kHeaderBytes;
  packet.length = length - kHeaderBytes;
  return true;
}

inline size_t encode(Kind kind, uint64_t session, uint32_t id, const uint8_t* bytes,
                     size_t length, uint8_t* output, size_t capacity) {
  if (!output || session == 0 || !validPayload(kind, id, bytes, length) ||
      capacity < kHeaderBytes + length) return 0;
  if (length) memmove(output + kHeaderBytes, bytes, length);
  output[0] = 1;
  output[1] = static_cast<uint8_t>(kind);
  for (unsigned i = 0; i < 8; ++i) output[2 + i] = static_cast<uint8_t>(session >> (8 * i));
  for (unsigned i = 0; i < 4; ++i) output[10 + i] = static_cast<uint8_t>(id >> (8 * i));
  return kHeaderBytes + length;
}

}  // namespace coc
}  // namespace observer
