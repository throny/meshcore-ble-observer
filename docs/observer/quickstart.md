# Public quickstart: P1-Pro BLE observer

This is a preparation guide, not a tested one-click installer. See the
[public project overview](public-export-readme.md) for scope and limitations.
The firmware is community custom firmware, not an
official MeshCore image. Solar/RF suitability and full recovery remain unproven.

## 1. Choose and review firmware

Use a downloadable artifact from the scheduled/manual
[Observer BLE Actions workflow](../../.github/workflows/observer-ble.yml) only
after reviewing its artifact name and `provenance.json` (Repeater tag and full
commit, patch series, toolchain, artifact hashes). Artifacts are short-lived
**unapproved candidates**. The current patch series is pinned to one upstream
version; if upstream advances incompatibly the workflow fails closed, rather
than making an older build appear current. A green build does not mean firmware
is supported, deployable, or safe for your device.

## 2. Back up before any USB flash

USB flashing is a separate manual operator action. Before starting, identify the
device explicitly and preserve the official recovery image and recovery plan.
Optionally capture public metadata/settings with the read-only helper:

```sh
install -d -m 700 "$HOME/p1pro-backups"
python3 tools/hardware/p1pro_public_backup.py \
  --port /dev/ttyACM0 --destination "$HOME/p1pro-backups"
```

Use the actual USB node you verified; do not infer device identity from its name.
On first use, leaving out `--expected-public-key` is explicit trust-on-first-use:
the helper checks the board and records the full public key, but cannot know that
it is the intended repeater. For later captures, pin the complete key (not a
prefix) with `--expected-public-key <64-hex-key>` and independently verify that
key through a trusted record.
The backup helper checks the board/public identity, sends only allowlisted read
commands and writes a caller-owned mode-0600 JSON file inside an existing
mode-0700 directory. The public-only snapshot is **not sufficient to restore
device identity**. It contains no private key/ACL recovery material, is not a
filesystem image and has not been restored or validated. Keep it private because
settings can still be sensitive. Stop on any mismatch or read error. The helper
does not reset, flash, or otherwise write to the device.

If identity recovery is specifically needed, add `--include-private` to the same
command. This is a separate opt-in: the helper prompts twice on the interactive
terminal (never argv/environment), requires GPG, and writes a second mode-0600
AES-encrypted artifact; the ordinary JSON file remains public-only. Keep the
password separately and securely. The encrypted artifact may help manual key
recovery but omits ACL/filesystem state, does not independently verify the
keypair, and is **not a complete or validated restore**. Never feed either key
material or password to a service installer. Do not use this recovery mode if
you cannot securely retain and later retrieve its password. If you have a trusted
full public-key record, also pass `--expected-public-key <64-hex-key>`; without
it the command uses the explicit trust-on-first-use behavior above and cannot
prove it is the intended physical repeater.

## 3. Flash only under an approved recovery plan

Select the exact supported board/application artifact and follow the vendor's
USB procedure with the correct bootloader/recovery material available. Verify
the result and identity using the approved procedure. No script here flashes,
and no Actions artifact authorizes a flash. Do not continue if board, bootloader,
artifact provenance, backup accessibility or rollback path is uncertain.

## 4. Enroll and pair securely

The observer firmware is P1-Pro/SenseCAP-specific. BLE observer mode is
default-off. Physical USB access is required to explicitly enable and enroll;
in the repeater's USB CLI run:

```text
observer status
observer enable
observer enroll
observer enrollment status
observer status
```

Use the generated passkey in the Linux host's pairing prompt while enrollment
is open. Enrollment cannot start while observer mode is off. Close an unused
window with `observer enrollment close`; `observer revoke` revokes the existing
peer and requires a fresh enrollment/pairing before it can observe again.
These are deliberate device configuration actions, not backup-tool commands.
Keep the
generated passkey private, verify the intended Linux host is paired/trusted and pinned,
and test revocation/re-pair under the approved device procedure. Never weaken
bond/trust checks or use a Companion as the observer endpoint.

## 5. Configure and install the bridge

Use the explicit BLE config/preflight in the
[generic Linux bridge guide](bridge-setup.md), not the generic shared serial
installer as if it were BLE-hardened. Keep application/venv root-owned
and non-service-writable; config is root-owned, service-group readable only
(directory `0750`, TOML `0640`). Installation, service enable/start, and config
writes are explicit operator actions; no automatic installer is provided.

Choose each broker independently. For each `[[broker]]`, set its own
`topics.iata`, distinct sanitized client identity and correct auth/TLS options.
Do not use test IATA values as production or assume one broker is a fallback for
the other. Production region/account/publication needs an operator-approved
mapping. Never put a device private key or pairing passkey in bridge config; the
trusted runtime uses the device key in memory for signing.

## 6. Diagnose, update, and roll back

Use the host guide's explicit config preflight before a separately authorized
service start; inspect `systemctl status` and the unit journal only on the
intended host. Verify device attribution, both independent destinations, clean
shutdown/reconnect and redaction. BLE ACK confirms complete host RAM
reassembly—not MQTT receipt—and there is no durable MQTT replay queue.

For updates, preserve the current known-good firmware/provenance and config,
review the newly resolved upstream pin and patch result, and repeat the security,
recovery and acceptance gates. Rollback is a deliberate operator action using
the verified board-specific recovery procedure. No unattended update or
rollback is implemented.
