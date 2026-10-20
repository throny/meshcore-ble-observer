"""Release identity for the bounded full-RAW-record candidate."""

import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.build import _patch_digest, load_patch_series, validate_observer_patch_scope

CANDIDATE = ROOT / "firmware/observer-lines544-release/observer-patch-v1.18.0"
LAST_PATCH = "0008-lossless-raw-records.patch"
PARENT_DIGEST = "0c73a52be44fb52944bcc837735fdc2f7617bd2adabd553681fcc46c6bcdd65d"


def test_lines544_release_is_complete_and_ordered():
    names = [p.name for p in sorted(CANDIDATE.glob("*.patch"))]
    assert len(names) == 8
    assert names[-1] == LAST_PATCH
    assert {"series.json", *names} <= {p.name for p in CANDIDATE.iterdir()}


def test_lines544_release_preserves_the_deployed_seven_patch_inputs():
    patches = sorted(CANDIDATE.glob("*.patch"))
    assert len(patches) == 8
    assert _patch_digest(patches[:-1]) == PARENT_DIGEST


def test_lines544_release_uses_existing_pin_and_scope_guards():
    series = load_patch_series(CANDIDATE)
    assert series.version == "1.18.0"
    assert series.upstream_commit_sha == "81dbae592ffad40b4212bc0b37e82b38da08f4bc"
    assert series.manifest_sha256 == "b826a86b511ba4b169da0c89180d86c974027a92b1c01c884daf3c78b0774ed3"
    validate_observer_patch_scope(sorted(CANDIDATE.glob("*.patch")))
    paths = set(re.findall(r"^\+\+\+ b/(\S+)$", (CANDIDATE / LAST_PATCH).read_text(), re.M))
    assert paths == {
        "src/observer/ObserverProtocol.h", "src/observer/ObserverRecordQueue.h",
        "src/observer/ObserverTransport.cpp", "src/observer/ObserverTransport.h",
        "test/test_observer_transport/test_stream_queue.cpp",
    }


def test_lines544_release_has_distinct_complete_provenance():
    patches = sorted(CANDIDATE.glob("*.patch"))
    assert len(patches) == 8
    reference = hashlib.sha256()
    for patch in patches:
        reference.update(patch.name.encode() + b"\0" + patch.read_bytes())
    assert _patch_digest(patches) == reference.hexdigest()
    assert reference.hexdigest() != PARENT_DIGEST
