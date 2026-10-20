#!/usr/bin/env python3
"""Build a pinned MeshCore repeater baseline and custom observer image locally.

This command intentionally has no upload, flash, OTA, or DFU operation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from observer_build.build import BuildError, build_release, validate_output_dir
from observer_build.release_resolver import resolve_official_latest_repeater_release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="clean detached upstream checkout")
    parser.add_argument("output", type=Path, help="fresh absent/empty directory for artifacts and provenance; never reused")
    parser.add_argument("--patch-dir", type=Path, required=True, help="maintained observer patch directory")
    parser.add_argument("--compare-stock", action="store_true", help="also compile the unchanged official stock target")
    arguments = parser.parse_args()
    try:
        validate_output_dir(arguments.output)
        release = resolve_official_latest_repeater_release()
        manifest = build_release(
            arguments.source,
            arguments.output,
            release=release,
            patch_dir=arguments.patch_dir,
            compare_stock=arguments.compare_stock,
        )
    except BuildError as error:
        parser.error(str(error))
    print(arguments.output / "provenance.json")
    for name, digest in manifest.artifacts.items():
        print(f"{name} SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
