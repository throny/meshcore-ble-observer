#pragma once

#include "Server.h"

namespace observer {
namespace coc {

// S140 adapter. Authorization is deliberately delegated to the existing
// observer policy only after both Bluefruit and GAP report a bonded MITM link.
// Callback hooks keep loop scheduling/stats owned by the selected integration.
class NordicPort final : public Port {
 public:
  using Authorize = bool (*)(uint16_t);
  using Stats = size_t (*)(char*, size_t);
  using Wake = void (*)();
  using Schedule = void (*)(uint32_t, bool);

  NordicPort(Authorize authorize, Stats stats, Wake wake, Schedule schedule)
      : authorize_(authorize), stats_(stats), wake_(wake), schedule_(schedule) {}

  uint32_t authorizes(uint16_t handle) override {
    ble_gap_conn_sec_t security{};
    const uint32_t result = sd_ble_gap_conn_sec_get(handle, &security);
    if (result != NRF_SUCCESS) return result;
    if (security.sec_mode.sm != 1 || security.sec_mode.lv < 3)
      return BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHENTICATION;
    // The callback must perform the application's bonded/secured and enrolled
    // peer check before it may read getPeerAddr().
    return authorize_ && authorize_(handle) ? BLE_L2CAP_CH_STATUS_CODE_SUCCESS
                                            : BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHORIZATION;
  }

  uint32_t accept(uint16_t handle, uint16_t& cid, uint8_t* rx, uint16_t length) override {
    ble_l2cap_ch_setup_params_t params{};
    params.status = BLE_L2CAP_CH_STATUS_CODE_SUCCESS;
    params.rx_params.rx_mtu = length;
    params.rx_params.rx_mps = BLE_L2CAP_MPS_MIN;
    params.rx_params.sdu_buf = {rx, length};
    return sd_ble_l2cap_ch_setup(handle, &cid, &params);
  }
  void refuse(uint16_t handle, uint16_t cid, uint16_t status) override {
    ble_l2cap_ch_setup_params_t params{};
    params.status = status;
    (void)sd_ble_l2cap_ch_setup(handle, &cid, &params);
  }
  uint32_t receive(uint16_t handle, uint16_t cid, uint8_t* rx, uint16_t length) override {
    ble_data_t data{rx, length};
    return sd_ble_l2cap_ch_rx(handle, cid, &data);
  }
  uint32_t send(uint16_t handle, uint16_t cid, const uint8_t* bytes, uint16_t length) override {
    ble_data_t data{const_cast<uint8_t*>(bytes), length};
    return sd_ble_l2cap_ch_tx(handle, cid, &data);
  }
  void release(uint16_t handle, uint16_t cid) override {
    (void)sd_ble_l2cap_ch_release(handle, cid);
  }
  size_t stats(char* bytes, size_t capacity) override {
    return stats_ ? stats_(bytes, capacity) : 0;
  }
  void wake() override { if (wake_) wake_(); }
  void schedule(uint32_t delay_ms, bool enabled) override {
    if (schedule_) schedule_(delay_ms, enabled);
  }

 private:
  Authorize authorize_;
  Stats stats_;
  Wake wake_;
  Schedule schedule_;
};

}  // namespace coc
}  // namespace observer
