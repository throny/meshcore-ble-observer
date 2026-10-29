# Building v1.18.0-coc

Most users should use the [prerelease download](https://github.com/throny/meshcore-ble-observer/releases/tag/v1.18.0-coc).
The builder below is for developers replaying the recorded Linux build. It never
flashes a device.

## Prerequisites

- Linux x86_64, Python 3.12+, Git and bubblewrap.
- A cached MeshCore Git repository containing the commit in
  [`observer-coc-release.json`](../observer-coc-release.json).
- PlatformIO 6.2.0 in a virtual environment, plus the recipe's cached Nordic
  platform, framework, compiler and libraries.
- A checkout of the matching [CoC host source](https://github.com/throny/meshcoretomqtt/tree/feature/repeater-observer-coc-20261029).
  The builder records a host-source snapshot for development; it is not part of
  the firmware image.

The recipe verifies cache fingerprints, not just version labels. A different
cache installation will be rejected even if its package versions look the same.
There is currently no automated cache bootstrap: this is an exact-input replay,
not a clean-machine installation guide. Prepare caches before running the builder;
network access is disabled during the build.

## Build

From the repository root, substitute your cache and checkout paths:

```sh
python3 tools/build_coc_release.py \
  --upstream /path/to/cached/MeshCore \
  --output out/coc-release \
  --packages /path/to/platformio/packages \
  --platforms /path/to/platformio/platforms \
  --dependencies /path/to/cached/environment/libdeps \
  --pio-venv /path/to/platformio-venv \
  --host /path/to/meshcoretomqtt \
  --build-date '29 Oct 2026'
```

The output directory must not already exist. The builder reads the pinned Git
objects, applies the recorded patches, and checks that the resulting runtime
matches the maintained source. It does not use or modify the upstream checkout's
working files. Shared SDK/toolchain files are read-only inside the sandbox.

Images appear under `out/coc-release/artifacts/`. `provenance.json` records source
hashes, tool inputs, build date, memory usage, and image checksums. The builder
checks the version/date in the ELF, application-only DFU contents, and UF2
application bounds. Compiler timestamps and ZIP metadata use the explicit build
date so wall-clock time does not change those inputs.

## Software tests

Install pytest in your development environment, then run:

```sh
python3 -m pytest tests/observer_build/test_coc_release.py tests/observer_build/test_coc_core.py
```

Native tests require `g++`. SDK-dependent checks are skipped unless you provide
`OBSERVER_COC_FRAMEWORK_SOURCE` and `OBSERVER_COC_CMSIS_INCLUDE`. The queue and
overlay checks additionally use `OBSERVER_COC_QUEUE_SOURCE` and
`OBSERVER_COC_SELECTED_SOURCE` from the pre-overlay patched baseline. The optional
ARM object checks use `OBSERVER_COC_ARM_CXX`.

These tests do not prove board compatibility, recovery, power use, or sustained
radio performance. Build verification is not permission to flash.
