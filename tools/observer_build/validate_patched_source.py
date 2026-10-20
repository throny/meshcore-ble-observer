#!/usr/bin/env python3
"""Validate release-bound observer integration on an already patched checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observer_build.build import BuildError, validate_patched_observer_source
from observer_build.release_resolver import ReleasePin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="exact patched artifact checkout")
    parser.add_argument("--patch-dir", type=Path, required=True, help="explicit versioned observer series")
    parser.add_argument("--release-metadata", type=Path, required=True, help="resolved tag/full-SHA JSON")
    arguments = parser.parse_args()
    try:
        payload = json.loads(arguments.release_metadata.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"tag", "commit_sha"}:
            raise BuildError("Resolved release metadata must contain only tag and commit_sha")
        if not isinstance(payload["tag"], str) or not isinstance(payload["commit_sha"], str):
            raise BuildError("Resolved release metadata tag and commit_sha must be strings")
        release = ReleasePin(payload["tag"], payload["commit_sha"])
        validate_patched_observer_source(arguments.source, arguments.patch_dir, release)
    except (OSError, json.JSONDecodeError, BuildError) as error:
        print(f"patched observer source validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Validated {release.tag} at {release.commit_sha} with {arguments.patch_dir.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
