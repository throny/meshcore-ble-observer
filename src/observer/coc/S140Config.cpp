// This optional object supplies the experimental framework hook. It does not
// accept a channel or replace the observer's security/enrollment policy.
#if defined(SENSECAP_OBSERVER_COC_CHANNELS)

#include <ble.h>

#if SENSECAP_OBSERVER_COC_CHANNELS != 1 && SENSECAP_OBSERVER_COC_CHANNELS != 2
#error "The observer CoC experiment supports one or two channels only"
#endif

extern "C" uint32_t bluefruit_configure_peripheral(uint8_t config_tag, uint32_t ram_start) {
  ble_cfg_t config = {};
  config.conn_cfg.conn_cfg_tag = config_tag;
  config.conn_cfg.params.l2cap_conn_cfg.ch_count = SENSECAP_OBSERVER_COC_CHANNELS;
  config.conn_cfg.params.l2cap_conn_cfg.rx_mps = 128;
  config.conn_cfg.params.l2cap_conn_cfg.tx_mps = 128;
  config.conn_cfg.params.l2cap_conn_cfg.rx_queue_size = 1;
  config.conn_cfg.params.l2cap_conn_cfg.tx_queue_size = 1;
  return sd_ble_cfg_set(BLE_CONN_CFG_L2CAP, &config, ram_start);
}

#endif
