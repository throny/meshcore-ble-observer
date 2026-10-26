# Linux host setup

This is a manual setup guide for the experimental BLE observer. There is no
supported one-command BLE installer. The standard USB installer and service are
not a supported BLE setup. Decide which device, settings and services to change
before running anything; software tests do not perform those steps for you.

## Supported hardware scope

| Component | Support statement |
|---|---|
| Repeater | SenseCAP Solar / P1-Pro with this custom firmware, not an official MeshCore image. Other radio boards are untested. |
| Host | Linux, Python 3.11+, BlueZ and a working BLE adapter. Not every board or Linux distribution has been tested. |
| MQTT | Configure and check each destination separately. One working broker does not prove another receives data. |

The bridge is based on `Cisien/meshcoretomqtt`. Automated builds record the exact
upstream revision and observer changes, then run tests. If the changes no longer
apply, the build reports a conflict rather than hiding it. Firmware builds use
the latest stable Repeater tag. Both outputs are test candidates, not supported
releases, and neither workflow installs them on your devices.

## Manual setup outline

1. Prepare a Linux host with Python 3.11+, BlueZ and a working Bluetooth adapter.
   Use its normal package manager; this guide does not replace the OS or change
   networking.
2. Choose and review the exact bridge source, observer changes and test results.
   Start with clean source, not local deployment notes or backups.
3. Prepare the P1-Pro using the [device quickstart](quickstart.md). Have a known
   recovery method before flashing it.
4. Open enrollment over USB, pair through the host OS, and verify that the
   intended host is bonded, trusted and pinned. Keep the passkey private; do not
   weaken those checks to make a connection work.
5. Create one explicit BLE TOML config from the current reviewed example. Set
   `[general].transport = "ble"` and the exact bonded peer address in `[ble]`.
   Leave `remote_serial.enabled = false` unless you need and have reviewed that
   feature. Validate the same file you will give the running bridge:

   ```sh
   python3 preflight_config.py --config /path/to/observer-config.toml
   ```

   This checks configuration only; it does not connect to BlueZ/MQTT or prove
   that data arrives.
6. Configure each `[[broker]]` with its endpoint, authentication, verified TLS
   and correct region code. Set its own `[broker.topics].iata` if needed, and use
   unique client IDs. Confirm the service's region rules; do not publish test
   values or assume one entry sends to several regions. Multiple regions require
   separate intended entries and appropriate client IDs.
7. Test the bridge manually before setting up a service:

   ```sh
   python3 mctomqtt.py --config /path/to/observer-config.toml
   ```

   Use a virtual environment and the reviewed dependency lock. Keep application
   files root-owned so the service cannot change its own code. Config should be
   root-owned and readable only by its service group: directory `0750`, files
   `0640`. Never put device private keys or pairing passkeys in that config.
8. Before enabling a service, review its permissions and exact configuration.
   Test startup, clean stop, reconnect, sensitive-data handling and rollback on
   that host. The included templates are not a tested universal installer.

## Update and rollback

Keep the known-good firmware, source revision, config and recovery instructions
before replacing either side. Review and test the exact new build; firmware
updates also need the board-specific recovery checks in the quickstart.

Nothing here automatically flashes hardware, enables services or rolls back an
update. A BLE acknowledgement means a complete record reached host RAM, not that
MQTT received it. There is no durable MQTT replay queue. Untested hardware remains
experimental even if a software build passes.
