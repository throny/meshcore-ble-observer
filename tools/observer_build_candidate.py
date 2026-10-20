#!/usr/bin/env python3
"""Build an unapproved candidate from the newest resolved stable Repeater release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from observer_build.candidate import CandidateError, run_candidate_build
from observer_build.release_resolver import ReleasePin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="clean detached checkout at the resolved upstream SHA")
    parser.add_argument("output", type=Path, help="fresh output directory for candidate artifacts and report")
    parser.add_argument("--patch-dir", type=Path, required=True, help="reviewed pinned series; copied unchanged into a temporary retarget")
    parser.add_argument("--release-json", type=Path, required=True, help="local JSON containing exact tag and full commit SHA")
    parser.add_argument("--build-date", required=True, help="firmware CLI date, e.g. '19 Oct 2026'")
    args = parser.parse_args()
    try:
        payload = json.loads(args.release_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"tag", "commit_sha"}:
            raise CandidateError("Resolved release file must contain only tag and commit_sha")
        if not isinstance(payload["tag"], str) or not isinstance(payload["commit_sha"], str):
            raise CandidateError("Resolved release tag and commit SHA must be strings")
        if not re.fullmatch(r"[0-9a-f]{40}", payload["commit_sha"].lower()):
            raise CandidateError("Resolved commit SHA must be a full immutable 40-character SHA")
        result = run_candidate_build(
            args.source,
            args.output,
            args.patch_dir,
            ReleasePin(payload["tag"], payload["commit_sha"].lower()),
            build_date=args.build_date,
        )
        print(f"Unapproved candidate report: {args.output / 'candidate-report.json'}")
        print("No flash or stable release was performed.")
        return 0
    except (CandidateError, OSError, ValueError) as error:
        print(f"Candidate rejected: {error}", file=sys.stderr)
        if args.output.exists() and (args.output / "candidate-report.json").is_file():
            print(f"Actionable report: {args.output / 'candidate-report.json'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
