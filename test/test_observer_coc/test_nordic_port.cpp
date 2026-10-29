#include "observer/coc/NordicPort.h"
#include <assert.h>
#include <stdio.h>

static ble_gap_conn_sec_t security{};
static uint16_t seen_cid = 0;
static ble_l2cap_ch_setup_params_t seen_setup{};
static ble_data_t seen_data{};
static unsigned authorized = 0;
static bool authorize(uint16_t handle) { ++authorized; return handle == 7; }
static size_t stats(char*, size_t) { return 0; }
static void noop() {}
static void schedule(uint32_t, bool) {}

extern "C" uint32_t sd_ble_gap_conn_sec_get(uint16_t, ble_gap_conn_sec_t* out) {
  *out = security; return NRF_SUCCESS;
}
extern "C" uint32_t sd_ble_l2cap_ch_setup(uint16_t, uint16_t* cid,
                                            ble_l2cap_ch_setup_params_t const* params) {
  seen_cid = *cid; seen_setup = *params; return NRF_SUCCESS;
}
extern "C" uint32_t sd_ble_l2cap_ch_rx(uint16_t, uint16_t, ble_data_t const* data) {
  seen_data = *data; return NRF_SUCCESS;
}
extern "C" uint32_t sd_ble_l2cap_ch_tx(uint16_t, uint16_t, ble_data_t const* data) {
  seen_data = *data; return NRF_SUCCESS;
}
extern "C" uint32_t sd_ble_l2cap_ch_release(uint16_t, uint16_t) { return NRF_SUCCESS; }

int main() {
  observer::coc::NordicPort port(authorize, stats, noop, schedule);
  assert(port.authorizes(7) == BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHENTICATION);
  assert(authorized == 0);
  security.sec_mode.sm = 1; security.sec_mode.lv = 3; security.encr_key_size = 7;
  assert(port.authorizes(7) == BLE_L2CAP_CH_STATUS_CODE_SUCCESS && authorized == 1);
  assert(port.authorizes(8) == BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHORIZATION && authorized == 2);
  uint8_t rx[558]{}; uint16_t cid = 64;
  assert(port.accept(7, cid, rx, sizeof(rx)) == NRF_SUCCESS);
  assert(seen_cid == 64 && seen_setup.status == BLE_L2CAP_CH_STATUS_CODE_SUCCESS);
  assert(seen_setup.rx_params.sdu_buf.p_data == rx && seen_setup.rx_params.rx_mtu == sizeof(rx));
  port.refuse(7, cid, BLE_L2CAP_CH_STATUS_CODE_NO_RESOURCES);
  assert(seen_setup.status == BLE_L2CAP_CH_STATUS_CODE_NO_RESOURCES && !seen_setup.rx_params.sdu_buf.p_data);
  assert(port.receive(7, cid, rx, sizeof(rx)) == NRF_SUCCESS && seen_data.p_data == rx);
  const uint8_t tx[10]{};
  assert(port.send(7, cid, tx, sizeof(tx)) == NRF_SUCCESS && seen_data.p_data == tx);
  port.release(7, cid);
  puts("S140 API boundary, security gate, and buffer pointers PASS");
}
