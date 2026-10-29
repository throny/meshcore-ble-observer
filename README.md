# MeshCore Bluetooth observer firmware

Send a SenseCAP Solar / P1-Pro repeater's packet logs to a nearby Linux computer
over Bluetooth, without leaving a USB cable connected. The host can forward the
logs to MQTT using [meshcoretomqtt](https://github.com/Cisien/meshcoretomqtt/pull/74).

The connection uses Bluetooth LE credit-based channels (CoC). It reports what
the repeater hears, not observations from a separate Companion radio.

## Download

**[v1.18.0-coc — experimental prerelease](https://github.com/throny/meshcore-ble-observer/releases/tag/v1.18.0-coc)**  
Based on MeshCore repeater v1.18.0; built October 29, 2026.

| File | Purpose |
| --- | --- |
| `v1.18.0-coc.uf2` | USB application update |
| `v1.18.0-coc.zip` | BLE DFU application update, with a compatible bootloader |
| `SHA256SUMS` | Download checksums |
| `provenance.json` | Exact source and build inputs |

These are application-only images: they do not include a bootloader or SoftDevice
update. Confirm your board and bootloader, back up your identity, and have a
recovery method before flashing. This is community firmware, not an official
MeshCore release. Publishing an image does not update an installed device.

## Receive the logs

Use the [CoC host setup guide](https://github.com/throny/meshcoretomqtt/blob/feature/repeater-observer-coc-20261029/docs/coc.md).
It requires Linux/BlueZ, the Rust helper, and an [enrolled Bluetooth peer](docs/enrollment.md).
Select `general.transport = "coc"`; USB serial remains the host's default.

The repeater clock may need restoring after reboot. With `sync_time = true`, the
host sets UTC on connection after checking its own clock synchronization.

## Status and limits

The transport has been used on one P1-Pro/Linux setup. The October 29 download is
a newer build than the firmware installed during that testing. Broader hardware
support, power use, RF coexistence, and long-term reliability still need testing.
Records are buffered in RAM; a receipt does not guarantee storage or MQTT delivery.

## Build from source

`main` contains the firmware sources and pinned build recipe. The `v1.18.0-coc`
tag identifies the download's source. See [Building](firmware/observer-coc-experimental/README.md)
for prerequisites, commands, and software tests.
