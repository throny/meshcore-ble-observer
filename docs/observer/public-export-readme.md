# MeshCore BLE Repeater Observer — public candidate workspace

This clean source snapshot contains a custom community observer firmware
candidate for **SenseCAP Solar / P1-Pro** and a BLE-to-MQTT host adapter based on
upstream `Cisien/meshcoretomqtt`. It is not an official MeshCore image or a
claim that every repeater, BLE adapter, Pi model, Linux distribution or broker
is supported.

## Upstream candidate flow

- The firmware workflow resolves the latest numeric stable Repeater tag to an
  immutable Git SHA, retargets only a temporary candidate manifest, and keeps
  the reviewed patch bytes unchanged. Patch/source/native-test drift fails with
  a report; it does not silently build an older pin.
- The host workflow resolves the upstream bridge default branch to an immutable
  SHA, derives a scoped patch from its recorded baseline, applies it without a
  three-way fallback, privacy-checks the patch, and runs the frozen upstream
  test suite.
- Artifacts are short-lived **unapproved candidates**, not releases. No workflow
  flashes a repeater, installs/enables a host service, configures a production
  broker, pushes a branch, or opens an upstream PR.

## Prepare a device

Read [quickstart](docs/observer/quickstart.md) before any USB operation and
[generic host setup](docs/observer/bridge-setup.md) before configuring a Linux
BLE host. Firmware USB backup, pairing, flash/recovery, host-service changes and
production MQTT publication remain explicit operator actions. The public USB
snapshot is partial; optional identity recovery is separately GPG-encrypted and
not a proven restore.

The candidate job uses an explicit file allowlist and omits local worktree
history, hardware records, local configuration, operator recovery scripts and
backups. See [upstream contribution/privacy boundaries](docs/observer/upstream-contributions.md).
