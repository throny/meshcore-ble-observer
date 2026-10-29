#include "observer/coc/Protocol.h"
#include <assert.h>
#include <initializer_list>
#include <stdio.h>
#include <string.h>

using namespace observer::coc;

int main() {
  uint8_t wire[558]{};
  uint8_t body[544]{};
  body[0] = 0x20; body[1] = 2;  // 544, little endian.
  Packet packet;
  const Kind kinds[] = {Kind::Hello, Kind::Ready, Kind::Record, Kind::Receipt,
                        Kind::Command, Kind::Reply, Kind::StatsRequest, Kind::StatsReply};
  for (Kind kind : kinds) {
    const bool handshake = kind == Kind::Hello || kind == Kind::Ready;
    const size_t length = handshake ? 2 :
        ((kind == Kind::Receipt || kind == Kind::StatsRequest) ? 0 : 1);
    if (kind == Kind::Command) body[0] = 'g';
    else body[0] = 0x20;
    const uint32_t id = handshake ? 0 : 7;
    const size_t n = encode(kind, UINT64_C(0x8877665544332211), id, body, length, wire, sizeof(wire));
    assert(n == length + 14);
    const Direction direction = (kind == Kind::Ready || kind == Kind::Receipt ||
        kind == Kind::Command || kind == Kind::StatsRequest) ? Direction::ToDevice : Direction::ToHost;
    assert(decode(wire, n, direction, packet));
    assert(packet.kind == kind && packet.id == id && packet.session == UINT64_C(0x8877665544332211));
    assert(packet.length == length && packet.payload == wire + 14);
    assert(!decode(wire, n, direction == Direction::ToDevice ? Direction::ToHost : Direction::ToDevice, packet));
    assert(packet.payload == nullptr);  // No stale successful view on rejection.
  }
  for (size_t length : {size_t(1), size_t(16), size_t(541), size_t(544)}) {
    memset(body, 'x', length);
    assert(encode(Kind::Record, 1, 1, body, length, wire, sizeof(wire)) == length + 14);
    assert(decode(wire, length + 14, Direction::ToHost, packet));
    assert(memcmp(packet.payload, body, length) == 0);
  }
  assert(encode(Kind::Record, 0, 1, body, 1, wire, sizeof(wire)) == 0);
  assert(encode(Kind::Record, 1, 0, body, 1, wire, sizeof(wire)) == 0);
  assert(encode(Kind::Record, 1, 1, body, 0, wire, sizeof(wire)) == 0);
  assert(encode(Kind::Record, 1, 1, body, 545, wire, sizeof(wire)) == 0);
  assert(encode(Kind::Record, 1, 1, body, 544, wire, 557) == 0);
  assert(encode(Kind::Record, 1, 1, nullptr, 1, wire, sizeof(wire)) == 0);
  assert(encode(Kind::Receipt, 1, 1, nullptr, 0, wire, sizeof(wire)) == 14);
  assert(!decode(nullptr, 14, Direction::ToDevice, packet));
  assert(!decode(wire, 13, Direction::ToDevice, packet));
  assert(!decode(wire, 559, Direction::ToDevice, packet));
  wire[0] = 2; assert(!decode(wire, 14, Direction::ToDevice, packet));
  wire[0] = 1; wire[1] = 9; assert(!decode(wire, 14, Direction::ToDevice, packet));
  memset(body, 'g', sizeof(body));
  assert(encode(Kind::Command, 1, 1, body, 95, wire, sizeof(wire)) == 109);
  assert(encode(Kind::Command, 1, 1, body, 96, wire, sizeof(wire)) == 0);
  for (uint8_t bad : {uint8_t(0), uint8_t('\r'), uint8_t('\n')}) {
    body[3] = bad;
    assert(encode(Kind::Command, 1, 1, body, 4, wire, sizeof(wire)) == 0);
  }
  assert(encode(Kind::StatsReply, 1, 1, body, 128, wire, sizeof(wire)) == 142);
  assert(encode(Kind::StatsReply, 1, 1, body, 129, wire, sizeof(wire)) == 0);
  assert(encode(Kind::Receipt, 1, 1, body, 1, wire, sizeof(wire)) == 0);
  body[0] = 0x1f; body[1] = 2;
  assert(encode(Kind::Hello, 1, 0, body, 2, wire, sizeof(wire)) == 0);
  puts("protocol boundary/direction/identity checks PASS");
}
