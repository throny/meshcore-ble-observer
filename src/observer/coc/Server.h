#pragma once

#include <ble.h>
#include "Delivery.h"
#include "../ObserverCommand.h"

namespace observer {
namespace coc {

// The concrete port uses the existing stack and enrolled-peer policy. Native
// tests inject only this vendor boundary, not replacement BLE headers/stack.
struct Port {
  virtual ~Port() = default;
  virtual uint32_t authorizes(uint16_t handle) = 0;
  virtual uint32_t accept(uint16_t handle, uint16_t& cid, uint8_t* rx, uint16_t length) = 0;
  virtual void refuse(uint16_t handle, uint16_t cid, uint16_t status) = 0;
  virtual uint32_t receive(uint16_t handle, uint16_t cid, uint8_t* rx, uint16_t length) = 0;
  virtual uint32_t send(uint16_t handle, uint16_t cid, const uint8_t* bytes, uint16_t length) = 0;
  virtual void release(uint16_t handle, uint16_t cid) = 0;
  virtual size_t stats(char* bytes, size_t capacity) = 0;
  virtual void wake() = 0;
  virtual void schedule(uint32_t delay_ms, bool enabled) = 0;
};

class Server {
 public:
  static constexpr uint16_t kDataPsm = 0x80;
  static constexpr uint16_t kControlPsm = 0x81;
  explicit Server(Port& port, uint8_t channels) : port_(port), count_(channels) {}
  Server(const Server&) = delete;
  Server& operator=(const Server&) = delete;

  // Single BLE-task producer. RX bytes stay in their returned, unposted buffer;
  // completion flags are independent of bounded setup mailboxes and never lost
  // because a general event queue is full. No callback allocation or waiting.
  void event(const ble_evt_t& event) {
    const uint16_t type = event.header.evt_id;
    if (type == BLE_GAP_EVT_DISCONNECTED) {
      for (unsigned i = 0; i < count_ && i < 2; ++i) {
        const uint32_t bound = load(ch_[i].binding);
        if (bound != kUnbound && uint16_t(bound >> 16) == event.evt.gap_evt.conn_handle)
          signal(ch_[i], kReleased);
      }
      port_.wake(); return;
    }
    if (type < BLE_L2CAP_EVT_CH_SETUP_REQUEST || type > BLE_L2CAP_EVT_CH_TX) return;
    const auto& e = event.evt.l2cap_evt;
    if (type == BLE_L2CAP_EVT_CH_SETUP_REQUEST) {
      const auto& request = e.params.ch_setup_request;
      const int index = role(request.le_psm);
      if (index < 0) { reject(e.conn_handle, e.local_cid, BLE_L2CAP_CH_STATUS_CODE_LE_PSM_NOT_SUPPORTED); return; }
      Channel& c = ch_[index];
      uint8_t empty = 0;
      if (load(c.binding) != kUnbound || !__atomic_compare_exchange_n(&c.request_state,
          &empty, uint8_t(1), false, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {
        reject(e.conn_handle, e.local_cid, BLE_L2CAP_CH_STATUS_CODE_NO_RESOURCES); return;
      }
      c.request_handle = e.conn_handle; c.request_cid = e.local_cid;
      c.request_mtu = request.tx_params.tx_mtu;
      __atomic_store_n(&c.request_state, uint8_t(2), __ATOMIC_RELEASE);
      port_.wake(); return;
    }
    const uint32_t binding = (uint32_t(e.conn_handle) << 16) | e.local_cid;
    for (unsigned i = 0; i < count_ && i < 2; ++i) {
      Channel& c = ch_[i];
      if (load(c.binding) != binding) continue;
      switch (type) {
        case BLE_L2CAP_EVT_CH_SETUP:
          __atomic_store_n(&c.mtu, e.params.ch_setup.tx_params.tx_mtu, __ATOMIC_RELAXED);
          signal(c, kSetup); break;
        case BLE_L2CAP_EVT_CH_SETUP_REFUSED:
        case BLE_L2CAP_EVT_CH_RELEASED: signal(c, kReleased); break;
        case BLE_L2CAP_EVT_CH_CREDIT: signal(c, kCredit); break;
        case BLE_L2CAP_EVT_CH_RX:
          if (e.params.rx.sdu_buf.p_data != c.rx || __atomic_load_n(&c.rx_state, __ATOMIC_ACQUIRE) != 1) {
            signal(c, kFault); break;
          }
          __atomic_store_n(&c.rx_length, e.params.rx.sdu_len, __ATOMIC_RELAXED);
          __atomic_store_n(&c.rx_available, e.params.rx.sdu_buf.len, __ATOMIC_RELAXED);
          __atomic_store_n(&c.rx_state, uint8_t(2), __ATOMIC_RELEASE); break;
        case BLE_L2CAP_EVT_CH_TX: returned(c, e.params.tx.sdu_buf.p_data, false); break;
        case BLE_L2CAP_EVT_CH_SDU_BUF_RELEASED:
          returned(c, e.params.ch_sdu_buf_released.sdu_buf.p_data, true); break;
        default: break;
      }
      port_.wake(); return;
    }
  }

  template <typename Queue, typename Fragment = RecordFragment>
  void service(Queue* queue, uint64_t nonce, uint32_t now) {
    if (count_ != 1 && count_ != 2) return;
    processReturns(now);
    if (active_ && port_.authorizes(handle_) != BLE_L2CAP_CH_STATUS_CODE_SUCCESS) close();
    if (closing_) cleanup();
    for (unsigned i = 0; i < count_; ++i) {
      Channel& c = ch_[i];
      if (__atomic_load_n(&c.request_state, __ATOMIC_ACQUIRE) != 2) continue;
      const uint16_t handle = c.request_handle, cid = c.request_cid, mtu = c.request_mtu;
      __atomic_store_n(&c.request_state, uint8_t(0), __ATOMIC_RELEASE);
      uint32_t status = port_.authorizes(handle);
      if (!queue || !nonce || closing_ || load(c.binding) != kUnbound)
        status = BLE_L2CAP_CH_STATUS_CODE_NO_RESOURCES;
      if (mtu < kSduBytes) status = BLE_L2CAP_CH_STATUS_CODE_UNACCEPTABLE_PARAMS;
      if (status != BLE_L2CAP_CH_STATUS_CODE_SUCCESS) { reject(handle, cid, status); continue; }
      if (!active_) {
        uint64_t session = delivery_.pending() ? delivery_.session() : nonce;
        Fragment head;
        if (!delivery_.pending() && queue->peek(head, 1)) session = head.boot_nonce;
        if (!session || generation_ == UINT32_MAX || !delivery_.open(++generation_)) {
          reject(handle, cid, BLE_L2CAP_CH_STATUS_CODE_NO_RESOURCES); continue;
        }
        active_ = true; handle_ = handle; session_ = session; started_ = now;
      }
      if (handle != handle_) { reject(handle, cid, BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHORIZATION); continue; }
      c.setup = c.ready = c.hello = c.terminal = c.release_requested = false;
      __atomic_store_n(&c.rx_state, uint8_t(1), __ATOMIC_RELEASE);
      __atomic_store_n(&c.binding, (uint32_t(handle) << 16) | cid, __ATOMIC_RELEASE);
      uint16_t accepted_cid = cid;
      if (port_.accept(handle, accepted_cid, c.rx, kSduBytes) != NRF_SUCCESS || accepted_cid != cid) {
        __atomic_store_n(&c.rx_state, uint8_t(0), __ATOMIC_RELEASE);
        __atomic_store_n(&c.binding, kUnbound, __ATOMIC_RELEASE); close();
      }
    }
    if (active_ && !closing_) {
      if (!ready() && uint32_t(now - started_) >= 10000) { ++timeouts_; close(); }
      if (control_pending_ && uint32_t(now - control_started_) >= 15000) { ++timeouts_; close(); }
      if (request_id_ && !control_pending_ && uint32_t(now - request_started_) >= 15000) { ++timeouts_; close(); }
      if (delivery_.poll(now) == Action::Close) { ++timeouts_; close(); }
    }
    if (active_ && !closing_) {
      for (unsigned i = 0; i < count_; ++i) {
        Channel& c = ch_[i];
        if (c.setup && !c.hello && !control_pending_) {
          const uint8_t limit[] = {0x20, 2};
          queueControl(i, Kind::Hello, 0, limit, sizeof(limit), now);
        }
        if (__atomic_load_n(&c.rx_state, __ATOMIC_ACQUIRE) == 2) processRx(i, now);
      }
      if (!closing_ && ready() && queue && !delivery_.pending()) {
        Fragment head;
        if (queue->peek(head, 1)) {
          if (head.boot_nonce != session_) close();
          else if (!delivery_.load(*queue)) fail();
        }
      }
      if (!closing_) {
        sendControl(now);
        if (ready() && delivery_.ready(now) && (!control_pending_ || count_ == 2) &&
            !__atomic_load_n(&ch_[0].control_owned, __ATOMIC_ACQUIRE)) {
          __atomic_store_n(&data_pointer_, reinterpret_cast<uintptr_t>(delivery_.data()), __ATOMIC_RELEASE);
          __atomic_store_n(&ch_[0].data_owned, uint8_t(1), __ATOMIC_RELEASE);
          const uint32_t result = port_.send(handle_, cid(0), delivery_.data(), delivery_.size());
          if (result == NRF_SUCCESS) {
            if (!delivery_.submitAccepted(generation_, now)) fail();
          } else {
            __atomic_store_n(&ch_[0].data_owned, uint8_t(0), __ATOMIC_RELEASE);
            if (result == NRF_ERROR_RESOURCES || result == NRF_ERROR_BUSY) delivery_.resourcesBusy(generation_, now);
            else fail();
          }
        }
      }
    }
    if (closing_) { releaseChannels(); cleanup(); }
    uint32_t delay = 0; const bool timer = nextDelay(now, delay);
    port_.schedule(delay, timer);
  }

  bool takeCommand(char* out, size_t capacity) {
    if (!command_ready_ || !out || capacity <= command_length_ || closing_) return false;
    memcpy(out, command_, command_length_ + 1); command_ready_ = false; return true;
  }
  bool reply(const uint8_t* bytes, size_t length, uint32_t now) {
    if (!active_ || closing_ || !request_id_ || command_ready_ || control_pending_ ||
        port_.authorizes(handle_) != BLE_L2CAP_CH_STATUS_CODE_SUCCESS) return false;
    static const uint8_t empty[] = "(no output)";
    return queueControl(controlRole(), Kind::Reply, request_id_, length ? bytes : empty,
                        length ? length : sizeof(empty)-1, now);
  }
  void close() {
    if (!active_) return;
    closing_ = true; command_ready_ = false; request_id_ = 0; delivery_.close();
    if (!control_busy_) { control_pending_ = false; memset(control_, 0, sizeof(control_)); }
  }
  bool ready() const {
    return active_ && !closing_ && ch_[0].ready && (count_ == 1 || ch_[1].ready);
  }
  bool pending() const { return delivery_.pending(); }
  uint32_t rejected() const { return __atomic_load_n(&rejected_, __ATOMIC_ACQUIRE); }
  uint32_t faults() const { return faults_; }
  uint32_t timeouts() const { return timeouts_; }

  bool runnable(uint32_t now) const {
    for (unsigned i = 0; i < count_ && i < 2; ++i)
      if (__atomic_load_n(&ch_[i].request_state, __ATOMIC_ACQUIRE) == 2 || load(ch_[i].flags) ||
          __atomic_load_n(&ch_[i].rx_state, __ATOMIC_ACQUIRE) == 2) return true;
    if (closing_) return false;
    return (ready() && delivery_.ready(now)) || (control_pending_ && !control_busy_ && control_retry_);
  }

 private:
  static constexpr uint32_t kUnbound = UINT32_MAX;
  enum Flags : uint32_t { kSetup=1, kReleased=2, kCredit=4, kDataReturn=8, kControlReturn=16, kFault=32 };
  struct Channel {
    volatile uint32_t binding = kUnbound, flags = 0;
    volatile uint16_t mtu = 0, rx_length = 0, rx_available = 0;
    volatile uint8_t rx_state = 0, data_owned = 0, control_owned = 0, request_state = 0;
    uint16_t request_handle = 0, request_cid = 0, request_mtu = 0;
    bool setup=false, ready=false, hello=false, terminal=false, release_requested=false;
    uint8_t rx[kSduBytes]{};
  };
  static uint32_t load(const volatile uint32_t& value) { return __atomic_load_n(&value, __ATOMIC_ACQUIRE); }
  static void signal(Channel& c, uint32_t flag) { __atomic_fetch_or(&c.flags, flag, __ATOMIC_RELEASE); }
  unsigned controlRole() const { return count_ == 2 ? 1 : 0; }
  int role(uint16_t psm) const {
    if (count_ != 1 && count_ != 2) return -1;
    if (psm == kDataPsm) return 0;
    return count_ == 2 && psm == kControlPsm ? 1 : -1;
  }
  uint16_t cid(unsigned role) const { return uint16_t(load(ch_[role].binding)); }
  void reject(uint16_t handle, uint16_t cid, uint16_t status) {
    __atomic_fetch_add(&rejected_, 1U, __ATOMIC_RELAXED); port_.refuse(handle, cid, status);
  }
  void fail() { ++faults_; close(); }

  void returned(Channel& c, uint8_t* pointer, bool released) {
    if (pointer == c.rx) {
      if (__atomic_exchange_n(&c.rx_state, uint8_t(0), __ATOMIC_ACQ_REL) != 1) signal(c, kFault);
    } else if (pointer == control_) {
      if (__atomic_exchange_n(&c.control_owned, uint8_t(0), __ATOMIC_ACQ_REL) != 1) signal(c, kFault);
      else signal(c, kControlReturn);
    } else if (reinterpret_cast<uintptr_t>(pointer) == __atomic_load_n(&data_pointer_, __ATOMIC_ACQUIRE)) {
      if (__atomic_exchange_n(&c.data_owned, uint8_t(0), __ATOMIC_ACQ_REL) != 1) signal(c, kFault);
      else signal(c, kDataReturn);
    } else signal(c, kFault);
    if (released) signal(c, kFault); // Return ownership, then terminate the logical generation.
  }

  void processReturns(uint32_t now) {
    for (unsigned i = 0; i < count_; ++i) {
      Channel& c = ch_[i]; const uint32_t flags = __atomic_exchange_n(&c.flags, 0U, __ATOMIC_ACQ_REL);
      if (flags & kDataReturn) {
        if (!delivery_.returned(generation_, reinterpret_cast<const uint8_t*>(__atomic_load_n(&data_pointer_, __ATOMIC_ACQUIRE)), now)) fail();
      }
      if (flags & kControlReturn) {
        control_busy_ = control_pending_ = false; control_retry_ = true;
        if (control_kind_ == Kind::Reply || control_kind_ == Kind::StatsReply) request_id_ = 0;
        memset(control_, 0, sizeof(control_));
      }
      if (flags & kReleased) { c.terminal = true; close(); }
      if (flags & kSetup) {
        c.setup = true;
        if (__atomic_load_n(&c.mtu, __ATOMIC_ACQUIRE) < kSduBytes) fail();
        if (closing_) c.release_requested = false;
      }
      if (flags & kCredit) { control_retry_ = true; delivery_.resourcesAvailable(generation_); }
      if (flags & kFault) fail();
    }
  }

  bool queueControl(unsigned index, Kind kind, uint32_t id, const uint8_t* bytes, size_t length, uint32_t now) {
    if (control_pending_ || control_busy_) return false;
    const size_t n = encode(kind, session_, id, bytes, length, control_, sizeof(control_));
    if (!n) { fail(); return false; }
    control_length_ = n; control_target_ = index; control_kind_ = kind;
    control_pending_ = control_retry_ = true; control_started_ = now; return true;
  }
  void sendControl(uint32_t now) {
    if (!control_pending_ || control_busy_ || !control_retry_ || !ch_[control_target_].setup ||
        (control_target_ == 0 && delivery_.vendorOwned())) return;
    Channel& c = ch_[control_target_];
    control_busy_ = true;
    __atomic_store_n(&c.control_owned, uint8_t(1), __ATOMIC_RELEASE);
    const uint32_t result = port_.send(handle_, cid(control_target_), control_, control_length_);
    if (result == NRF_SUCCESS) {
      if (control_kind_ == Kind::Hello) c.hello = true;
    } else {
      __atomic_store_n(&c.control_owned, uint8_t(0), __ATOMIC_RELEASE); control_busy_ = false;
      if (result == NRF_ERROR_RESOURCES || result == NRF_ERROR_BUSY) control_retry_ = false;
      else fail();
    }
    (void)now;
  }

  void processRx(unsigned index, uint32_t now) {
    Channel& c = ch_[index];
    const uint16_t n = __atomic_load_n(&c.rx_length, __ATOMIC_ACQUIRE);
    const uint16_t available = __atomic_load_n(&c.rx_available, __ATOMIC_ACQUIRE);
    Packet packet;
    if (n > available || n > kSduBytes || !decode(c.rx, n, Direction::ToDevice, packet) || packet.session != session_) {
      fail(); return;
    }
    if (!c.ready) {
      if (packet.kind != Kind::Ready || !c.hello) { fail(); return; }
      c.ready = true;
    } else {
      if (count_ == 2 && index != controlRole()) { fail(); return; }
      if (packet.kind == Kind::Receipt) {
        delivery_.receipt(generation_, packet.session, packet.id);
      } else if (packet.kind == Kind::Command || packet.kind == Kind::StatsRequest) {
        if (control_pending_ || request_id_) return; // Hold unposted RX until bounded output capacity returns.
        if (packet.id <= last_request_id_) { fail(); return; }
        last_request_id_ = request_id_ = packet.id; request_started_ = now;
        if (packet.kind == Kind::StatsRequest) {
          char value[128]{}; const size_t length = port_.stats(value, sizeof(value));
          if (!length || length >= sizeof(value)) { fail(); return; }
          queueControl(controlRole(), Kind::StatsReply, request_id_, reinterpret_cast<uint8_t*>(value), length, now);
        } else {
          memcpy(command_, packet.payload, packet.length); command_[packet.length] = 0;
          uint32_t epoch = 0;
          if (!isAllowedReadCommand(command_) && !parseCanonicalEpoch(command_, epoch)) { fail(); return; }
          command_length_ = packet.length; command_ready_ = true;
        }
      } else { fail(); return; }
    }
    __atomic_store_n(&c.rx_state, uint8_t(1), __ATOMIC_RELEASE);
    if (port_.receive(handle_, cid(index), c.rx, kSduBytes) != NRF_SUCCESS) {
      __atomic_store_n(&c.rx_state, uint8_t(0), __ATOMIC_RELEASE); fail();
    }
  }

  void releaseChannels() {
    for (unsigned i = 0; i < count_; ++i) {
      Channel& c = ch_[i];
      if (load(c.binding) != kUnbound && !c.terminal && !c.release_requested) {
        c.release_requested = true; port_.release(handle_, cid(i));
      }
    }
  }
  void cleanup() {
    if (!closing_) return;
    bool all = true;
    for (unsigned i = 0; i < count_; ++i) {
      Channel& c = ch_[i];
      if (load(c.binding) == kUnbound) continue;
      if (!c.terminal || __atomic_load_n(&c.rx_state, __ATOMIC_ACQUIRE) == 1 ||
          __atomic_load_n(&c.data_owned, __ATOMIC_ACQUIRE) || __atomic_load_n(&c.control_owned, __ATOMIC_ACQUIRE)) { all = false; continue; }
      if (delivery_.vendorOwned() || control_busy_) { all = false; continue; }
      __atomic_store_n(&c.rx_state, uint8_t(0), __ATOMIC_RELEASE);
      __atomic_store_n(&c.binding, kUnbound, __ATOMIC_RELEASE);
      c.setup = c.ready = c.hello = c.terminal = c.release_requested = false;
    }
    if (all) {
      active_ = closing_ = false; control_pending_ = control_busy_ = command_ready_ = false;
      request_id_ = last_request_id_ = 0; memset(control_, 0, sizeof(control_));
    }
  }
  bool nextDelay(uint32_t now, uint32_t& delay) const {
    if (!active_ || closing_) return false;
    delay = 15000; bool needed = false;
    auto add = [&](uint32_t start, uint32_t interval) {
      const uint32_t elapsed = now - start;
      const uint32_t remaining = elapsed >= interval ? 0 : interval - elapsed;
      if (!needed || remaining < delay) delay = remaining;
      needed = true;
    };
    if (!ready()) add(started_, 10000);
    if (control_pending_) add(control_started_, 15000);
    if (request_id_ && !control_pending_) add(request_started_, 15000);
    uint32_t data = 0;
    if (delivery_.wakeDelay(now, data)) { if (!needed || data < delay) delay = data; needed = true; }
    return needed;
  }

  Port& port_;
  Channel ch_[2];
  Delivery delivery_;
  uint8_t control_[kSduBytes]{};
  char command_[96]{};
  volatile uintptr_t data_pointer_ = 0;
  volatile uint32_t rejected_ = 0;
  uint64_t session_ = 0;
  uint32_t generation_=0, started_=0, control_started_=0, request_started_=0;
  uint32_t request_id_=0, last_request_id_=0, faults_=0, timeouts_=0;
  uint16_t handle_=0, control_length_=0;
  uint8_t count_=0, control_target_=0, command_length_=0;
  Kind control_kind_ = Kind::Hello;
  bool active_=false, closing_=false, control_pending_=false, control_busy_=false;
  bool control_retry_=true, command_ready_=false;
};

}  // namespace coc
}  // namespace observer
