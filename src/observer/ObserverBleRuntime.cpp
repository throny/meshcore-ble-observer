#include "ObserverBleRuntime.h"

#if defined(SENSECAP_OBSERVER_COC_CHANNELS)
namespace observer {
namespace coc_runtime {
namespace {
EventCallback observer_callback = nullptr;
EventCallback legacy_callback = nullptr;
bool callback_installed = false;
bool begin_attempted = false;
bool begin_succeeded = false;

void dispatch(ble_evt_t* event) {
  EventCallback legacy = legacy_callback;
  EventCallback observer = observer_callback;
  if (legacy) legacy(event);
  if (observer) observer(event);
}

void ensureEventDispatcher() {
  if (!callback_installed) {
    Bluefruit.setEventCallback(dispatch);
    callback_installed = true;
  }
}
}

bool beginBluefruitOnce() {
  if (!begin_attempted) {
    begin_attempted = true;
    Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);
    begin_succeeded = Bluefruit.begin(1, 0);
  }
  return begin_succeeded;
}

void setObserverCallback(EventCallback callback) {
  observer_callback = callback;
  ensureEventDispatcher();
}

void setLegacyCallback(EventCallback callback) {
  legacy_callback = callback;
  ensureEventDispatcher();
}

}  // namespace coc_runtime
}  // namespace observer
#endif
