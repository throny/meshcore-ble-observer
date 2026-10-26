# MeshCore BLE Repeater Observer

Send a repeater's own packet logs to a nearby Linux computer over Bluetooth,
then forward them to MQTT. This project contains experimental firmware for
**SenseCAP Solar / P1-Pro** and a host adapter based on `Cisien/meshcoretomqtt`.

This is community firmware, not an official MeshCore release. Other boards and
hosts are untested; long-term reliability, power use and recovery are still being
evaluated. Receiving a record over BLE does not guarantee MQTT delivery.

## Getting started

1. Read the [device quickstart](docs/observer/quickstart.md), including its backup
   and recovery warnings, before flashing or pairing.
2. Follow the [Linux host guide](docs/observer/bridge-setup.md) to configure the
   bridge and each MQTT destination.
3. Review the exact firmware build and test it on your setup. There is no
   one-click installer or automatic device update.

## Builds and contributions

The automated workflow tries the observer changes against current upstream
firmware and bridge source. It records the exact revisions, runs tests and
reports conflicts instead of quietly using an older version. Its downloadable
artifacts are short-lived **test candidates**, not approved releases.

The workflow does not flash hardware, start services, configure live brokers
or submit upstream changes. See the
[contribution guide](docs/observer/upstream-contributions.md) for preparing a
focused patch without private settings, backups or local project history.
