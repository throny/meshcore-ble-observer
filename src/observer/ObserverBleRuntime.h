#pragma once

#if defined(SENSECAP_OBSERVER_COC_CHANNELS)
#include <bluefruit.h>

namespace observer {
namespace coc_runtime {

using EventCallback = void (*)(ble_evt_t*);

bool beginBluefruitOnce();
void setObserverCallback(EventCallback callback);
void setLegacyCallback(EventCallback callback);

}  // namespace coc_runtime
}  // namespace observer
#endif
