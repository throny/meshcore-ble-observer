# P1-Pro BLE observer quickstart

This guide is for the experimental SenseCAP Solar / P1-Pro observer, not stock
MeshCore firmware. Setup is manual. Read the [project overview](../../README.md)
first; power use, radio performance and full recovery are still being evaluated.

## 1. Choose and review firmware

Builds are available from the
[Observer BLE workflow](../../.github/workflows/observer-ble.yml). Before using
one, check its name and `provenance.json`: these identify the Repeater version,
exact source revision, observer patches, build tools and file hashes.

Downloads are short-lived **test candidates**, not approved releases. If newer
upstream source is incompatible, the workflow reports a failure instead of
silently using an older version. A successful build alone does not establish
that an image is safe for your device.

## 2. Back up before any USB flash

Identify the intended device and keep an official recovery image and a recovery
plan before flashing. You can save a partial, read-only settings snapshot with:

```sh
install -d -m 700 "$HOME/p1pro-backups"
python3 tools/hardware/p1pro_public_backup.py \
  --port /dev/ttyACM0 --destination "$HOME/p1pro-backups"
```

Replace the example port with the USB device you verified. A port or device name
alone does not prove which repeater it is. If you have a trusted identity record,
add `--expected-public-key <64-hex-key>` using the complete key, not a prefix.
Without that option, the helper records the key it finds but cannot establish
that this is your intended repeater (trust on first use).

The helper checks the board/identity, uses only permitted read commands and
writes a caller-owned `0600` JSON file in an existing `0700` directory. It does
not reset, flash or write to the device. Stop on a mismatch or read error.

**This is not a complete backup or a tested restore.** It contains no private
key or access-control recovery data and is not a filesystem image. Keep even
this public-only snapshot private: settings may still be sensitive.

### Optional encrypted identity capture

Only if you need private identity recovery, add `--include-private`. This needs
GPG and asks for the password twice in the interactive terminal, not in command
arguments or environment variables. It creates a second, AES-encrypted `0600`
file; the normal JSON stays public-only.

Keep its password separately and securely. Without it, the encrypted file will
not help you recover the key. This capture still omits access-control/filesystem
state, does not independently verify the keypair, and is **not a complete or
validated restore**. Use the trusted full-key check above when available. Never
give a service installer the private key or this password.

## 3. Check recovery before flashing

Choose the exact image for the supported board and follow its USB procedure,
with the correct bootloader and recovery files available. Check the device
identity afterward. Do not continue if you are unsure of the board, bootloader,
build origin, access to your backup or recovery method. The helper and build
workflow do not flash anything for you.

## 4. Enroll and pair securely

Observer mode is off by default. With physical USB access to the P1-Pro, run
these commands in its USB CLI to enable it and open enrollment:

```text
observer status
observer enable
observer enroll
observer enrollment status
observer status
```

Enter the generated passkey in the intended Linux host's pairing prompt while
enrollment is open. Keep it private and check that the host is paired, trusted
and pinned to the repeater. Enrollment cannot start while observer mode is off.

Close an unused window with `observer enrollment close`. `observer revoke`
removes the current peer; you must enroll and pair again before it can observe.
These commands change the device, unlike the read-only backup helper. Test your
revocation/re-pair procedure without weakening bond/trust checks. A Companion
is not the endpoint for this repeater observer protocol.

## 5. Configure and install the bridge

Follow the [Linux host guide](bridge-setup.md) using one explicit BLE config.
The standard USB installer is not a supported BLE installer. Keep application
files and the virtual environment root-owned so the service cannot change them.
Config should be root-owned and readable only by the service group: directory
`0750`, TOML `0640`.

Set each broker's endpoint, `topics.iata`, unique client ID and verified TLS/auth
options separately. Confirm each service's region rules instead of using test
values or assuming another broker's success proves delivery. Never put a device
private key or pairing passkey in config; the runtime signs using the device
key in memory. Starting a service and publishing to live brokers are steps you
choose explicitly, not actions performed by the build workflow.

## 6. Diagnose, update, and roll back

Run the config check before starting the service, then inspect its status and
journal on the intended host. Check the device identity, each MQTT destination,
clean shutdown/reconnect and logs for sensitive data. A BLE ACK confirms a
complete record reached host RAM, not MQTT receipt. There is no durable MQTT
replay queue.

Before updating, keep the working firmware, build details and config. Review
the new source/patches and repeat the security, recovery and hardware checks.
Rollback uses the verified board-specific recovery procedure; updates and
rollback are not automatic.
