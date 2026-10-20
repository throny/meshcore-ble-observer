import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.candidate import CandidateError, make_candidate_series, run_candidate_build
from observer_build.build import BuildError
from observer_build.release_resolver import ReleasePin


class CandidateSeriesTests(unittest.TestCase):
    def setUp(self):
        self.patch_dir = ROOT / "firmware/observer-lines544-release/observer-patch-v1.18.0"
        self.pin = ReleasePin("repeater-v1.19.2", "a" * 40)

    def test_retargets_manifest_without_changing_reviewed_patch_bytes(self):
        original_manifest = (self.patch_dir / "series.json").read_bytes()
        patches = {p.name: p.read_bytes() for p in self.patch_dir.glob("*.patch")}
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "observer-patch-v1.19.2"
            result = make_candidate_series(self.patch_dir, candidate, self.pin)
            manifest = json.loads((candidate / "series.json").read_text())
            self.assertEqual("1.19.2", manifest["version"])
            self.assertEqual(self.pin.tag, manifest["upstream_tag"])
            self.assertEqual(self.pin.commit_sha, manifest["upstream_commit_sha"])
            self.assertEqual(patches, {p.name: p.read_bytes() for p in candidate.glob("*.patch")})
            self.assertEqual(hashlib.sha256(original_manifest).hexdigest(), result.reviewed_manifest_sha256)
            self.assertEqual(original_manifest, (self.patch_dir / "series.json").read_bytes())

    def test_wrong_role_or_malformed_release_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CandidateError):
                make_candidate_series(self.patch_dir, Path(temporary) / "candidate",
                                      ReleasePin("companion-v1.19.2", "a" * 40))

    def test_does_not_overwrite_destination_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "occupied"
            destination.mkdir()
            marker = destination / "keep"
            marker.write_text("keep")
            with self.assertRaises(CandidateError):
                make_candidate_series(self.patch_dir, destination, self.pin)
            self.assertEqual("keep", marker.read_text())

    @patch("observer_build.native_compatibility.run_native_compatibility_tests")
    @patch("observer_build.candidate.build_release")
    def test_success_records_parent_and_temporary_series_and_version_flags(self, build, native):
        def fake_build(source, output, *, release, patch_dir):
            self.assertEqual(self.pin, release)
            self.assertEqual("observer-patch-v1.19.2", patch_dir.name)
            self.assertEqual("1.19.2", json.loads((patch_dir / "series.json").read_text())["version"])
            self.assertIn("v1.19.2-obs544", __import__("os").environ["PLATFORMIO_BUILD_FLAGS"])
            output.mkdir()
            (output / "provenance.json").write_text('{"build":"ok"}')
            return SimpleNamespace(artifacts={"observer.hex": "b" * 64})
        build.side_effect = fake_build
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = run_candidate_build(ROOT, output, self.patch_dir, self.pin, build_date="19 Oct 2026")
            self.assertEqual("unapproved-candidate", result["candidate_status"])
            self.assertEqual("passed", result["exact_source_native_tests"])
            self.assertEqual("b" * 64, result["artifacts"]["observer.hex"])
            report = json.loads((output / "candidate-report.json").read_text())
            self.assertIn("reviewed_series", report)
            self.assertIn("temporary_candidate_series", report)

    @patch("observer_build.candidate.build_release", side_effect=BuildError("patch mismatch at " + str(Path("/") / "home" / "synthetic-user" / "source")))
    def test_failure_writes_portable_no_fallback_report(self, _build):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            with self.assertRaises(CandidateError):
                run_candidate_build(ROOT, output, self.patch_dir, self.pin, build_date="19 Oct 2026")
            report = json.loads((output / "candidate-report.json").read_text())
            self.assertEqual("incompatible-or-build-failed", report["candidate_status"])
            self.assertIn("No older upstream pin was substituted.", report["limitations"])
            self.assertNotIn(str(Path("/") / "home" / "synthetic-user"), json.dumps(report))
