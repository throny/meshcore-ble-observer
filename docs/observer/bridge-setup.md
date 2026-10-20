# Generic Linux bridge setup boundary

This document describes a manual, reviewable Linux-host path. It is not a
one-click installer, does not imply support for every generic host model/distribution, and
does not authorize a BLE pairing change, production MQTT publication, service
installation, or firmware flash.

This snapshot does not currently provide a supported one-command BLE installer.
The inherited upstream installer and shared serial unit are **not** BLE-hardened.
Any service installation, enable/start, pairing change, production configuration, and rollback
must be chosen explicitly by the device owner; offline source tests perform none
of these operations.

## Supported hardware scope

| Component | Support statement |
|---|---|
| Repeater firmware | SenseCAP Solar / P1-Pro target only. This is custom community firmware, not an official MeshCore image. |
| BLE observer protocol | Board-side implementation is currently P1-Pro-specific. Other repeater boards/radios are experimental and unvalidated; do not assume a build or flash path exists for them. |
| Bridge host | Intended for a Linux system with Python 3.11+, BlueZ, and a supported BLE adapter. This describes requirements, not certification of every generic board or distro. |
| Broker destinations | Independent per-broker configuration. A broker's reachability or receipt is not inferred from another broker's success. |

The bridge is derived from upstream `Cisien/meshcoretomqtt`. The scheduled
candidate job snapshots an immutable upstream commit, exports only an explicit
code/test/package allowlist, applies the observer overlay without fuzzy or
three-way fallback, and runs the frozen upstream tests. A conflict produces a
report requiring a reviewed port. This does not silently replace a device with
a different upstream baseline. The firmware candidate workflow separately
starts from the latest official stable Repeater tag and pins its resolved SHA.
Both artifacts are candidates, not supported releases.

## Manual setup outline

1. Install a supported Linux distribution with Python 3.11+ and BlueZ using its
   normal package manager. Confirm its Bluetooth adapter is available and its
   OS supports ordinary bonded-device pairing/trust. This guide does not alter
   system packages or host networking.
2. Review the `meshcoretomqtt` source, exact upstream commit, observer overlay
   SHA and candidate tests. Use a clean upstream-based source checkout with the
   reviewed overlay; do not deploy historical project notes, operator backups,
   or local environment templates.
3. Prepare the P1-Pro only using the separate [firmware and USB backup
   quickstart](quickstart.md). Flashing remains a deliberate physical operation
   with a verified recovery path.
4. Use the firmware's explicit USB enrollment ceremony and the host OS pairing
   prompt. Verify the intended peer is bonded/trusted and pinned. Keep passkeys
   private. Do not weaken bonding or identity checks to make setup easier.
5. Create one explicit BLE TOML config from the current reviewed example. Set
   `[general].transport = "ble"` and the exact bonded peer address in `[ble]`.
   Keep `remote_serial.enabled = false` unless independently required and
   reviewed. Validate the same single file that the process will use:

   ```sh
   python3 preflight_config.py --config /path/to/observer-config.toml
   ```

   The validator does not connect to BlueZ or MQTT and does not test delivery.
6. Configure each `[[broker]]` independently: explicit endpoint, correct
   authentication/TLS and its own `[broker.topics].iata` where it differs from
   the global value. Use unique client identities. Never copy a production
   preset or choose an IATA/region by guess; do not use placeholders or test
   values for live publication. For one broker using multiple regions, create
   the separately intended broker entries with their own topic IATA/client IDs;
   do not assume a single destination fans out to multiple regions.
7. Run the bridge manually with the validated file before considering a service
   wrapper:

   ```sh
   python3 mctomqtt.py --config /path/to/observer-config.toml
   ```

   Use a virtual environment with the reviewed frozen dependency lock. Keep
   application files root-owned/non-service-writable and secret-bearing config
   root-owned with service-group-only read access (directory `0750`, files
   `0640`). The inherited generic serial installer is not a BLE installer.
8. Only after an independently reviewed service design and explicit operator
   approval, create a least-privilege service. Validate its effective config,
   startup, clean stop, reconnect, log redaction and rollback on that exact host.
   No generic unit is claimed to be enabled or field-validated by this guide.

## Update and rollback

For bridge updates, review the immutable upstream commit and exact overlay
artifact, run the candidate tests, then stage an explicit operator-approved
update. For firmware updates, review the stable Repeater tag/SHA, temporary
candidate manifest and provenance, and complete the board-specific acceptance
and recovery gates before any flash. Retain the known-good firmware, source
identity, config and rollback instructions before replacing either side.

There is no automatic firmware flash, service enable, production broker setup,
or rollback. BLE record ACK means complete host RAM reassembly, not MQTT receipt;
the bridge does not add durable MQTT replay. A generic host board or other radio
that has not been tested must remain an explicitly experimental deployment.
