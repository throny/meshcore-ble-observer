"""The selected release must resolve to the actual archived patch/host bytes."""

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.build import _patch_digest, validate_patch_series_release
from observer_build.release_resolver import ReleasePin


def test_selected_baseline_binds_exact_series_host_and_protocol():
    selected = json.loads((ROOT / "firmware/observer-baseline.json").read_text())
    assert selected["schema_version"] == 1
    assert selected["release"] == {
        "tag": "repeater-v1.18.0",
        "commit_sha": "81dbae592ffad40b4212bc0b37e82b38da08f4bc",
    }
    patch_dir = ROOT / selected["patch_directory"]
    assert patch_dir == ROOT / "firmware/observer-lines544-release/observer-patch-v1.18.0"
    pin = ReleasePin(**selected["release"])
    assert validate_patch_series_release(patch_dir, pin) is not None
    assert _patch_digest(sorted(patch_dir.glob("*.patch"))) == selected["patch_sha256"]
    assert selected["patch_sha256"] == "9ddbd449b422aed28cff949da8bc4c6d87f3294bb93d16e281482612a742b00c"
    receiver = ROOT / selected["host"]["receiver_path"]
    assert hashlib.sha256(receiver.read_bytes()).hexdigest() == selected["host"]["receiver_sha256"]
    assert selected["host"]["receiver_sha256"] == "331bcd27b017288cac3f9d570cb61ce2da9c67c918e80ca0cecb7a262b3436e1"
    assert selected["protocol"] == {
        "frame_header_bytes": 16,
        "maximum_record_bytes_including_terminator": 544,
        "maximum_notification_bytes": 244,
        "maximum_fragment_payload_bytes": 228,
    }
    assert selected["firmware_source_authority"] == "strict-replay-of-selected-series"
    assert selected["deployment_approved"] is False
