#!/usr/bin/env python3
"""Prepare and test a scoped observer overlay on a specific upstream bridge commit."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from observer_build.bridge_candidate import (
    BRIDGE_BASE_SHA, BridgeCandidateError, prepare_bridge_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-repo", type=Path, required=True, help="detached checkout at the recorded upstream bridge base")
    parser.add_argument("--latest-repo", type=Path, required=True, help="detached checkout at the freshly resolved upstream commit")
    parser.add_argument("--overlay-repo", type=Path, default=Path(__file__).resolve().parents[1], help="local observer repository")
    parser.add_argument("--overlay-package", type=Path, default=Path(__file__).resolve().parents[1] / "observer/meshcoretomqtt")
    parser.add_argument("--output", type=Path, required=True, help="fresh output directory for overlay and result report")
    parser.add_argument("--base-sha", default=BRIDGE_BASE_SHA, help="expected exact upstream baseline commit")
    parser.add_argument("--run-tests", action="store_true", help="resolve frozen test dependencies and run offline tests in the patched candidate")
    parser.add_argument("--test-python", type=Path, help="run offline tests with this existing interpreter without syncing/installing dependencies")
    parser.add_argument("--firmware-source", type=Path, help="exact patched firmware source for cross-layer tests")
    args = parser.parse_args()
    try:
        result = prepare_bridge_candidate(
            args.base_repo, args.latest_repo, args.overlay_repo, args.overlay_package,
            args.output, base_sha=args.base_sha, run_tests=args.run_tests,
            test_python=args.test_python,
            firmware_source=args.firmware_source,
        )
        print(f"Bridge candidate: {args.output / 'bridge-candidate-report.json'}")
        print(f"Scoped overlay SHA-256: {result['overlay_patch_sha256']}")
        return 0
    except BridgeCandidateError as error:
        print(f"Bridge candidate rejected: {error}", file=sys.stderr)
        report = args.output / "bridge-candidate-report.json"
        if report.is_file():
            print(f"Actionable report: {report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
