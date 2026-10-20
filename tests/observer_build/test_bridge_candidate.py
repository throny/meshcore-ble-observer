import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.bridge_candidate import (
    BridgeCandidateError, _privacy_findings, _safe_error,
    prepare_bridge_candidate,
)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def repo(root, files):
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Fixture")
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", "fixture")
    git(root, "checkout", "--quiet", "--detach")
    return git(root, "rev-parse", "HEAD")


class BridgeCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        base_files = {
            "bridge/transport.py": "BASE = 1\n",
            "bridge/other.py": "UNCHANGED = True\n",
            "tests/test_transport.py": "def test_base(): pass\n",
            "README.md": "upstream docs\n",
        }
        self.base = self.root / "base"
        self.base_sha = repo(self.base, base_files)
        self.latest = self.root / "latest"
        latest_files = {**base_files, "bridge/other.py": "UNCHANGED = True\nUPSTREAM = True\n"}
        self.latest_sha = repo(self.latest, latest_files)
        self.overlay = self.root / "overlay"
        repo(self.overlay, {
            "observer/meshcoretomqtt/bridge/transport.py": "OBSERVER = 2\n",
            "observer/meshcoretomqtt/tests/test_transport.py": "def test_observer(): assert True\n",
            "observer/meshcoretomqtt/README.md": "PRIVATE LOCAL DEPLOYMENT DETAIL\n",
        })
        self.output = self.root / "candidate"

    def tearDown(self):
        self.temporary.cleanup()

    def test_privacy_review_reports_categories_without_echoing_values(self):
        # Construct deliberately unsafe *synthetic* fixtures so the distributed
        # test sources themselves contain no deployable endpoint or peer.
        sample = " ".join([
            str(Path("/") / "home" / "synthetic-user"),
            str(Path("/") / "opt" / "sample-observer"),
            "-".join(["AB", "CD", "SyntheticNode123"]),
            ".".join(map(str, [10, 20, 30, 40])),
            ":".join(["2001", "db8", "", "2"]),
            ":".join(["AA", "BB", "CC", "DD", "EE", "FF"]),
        ])
        findings = _privacy_findings(sample)
        self.assertEqual({
            "operator-device-identity", "operator-home-path",
            "embedded-ip-address", "non-placeholder-ble-address",
            "deployment-specific-os-or-path",
        }, findings)
        self.assertNotIn(sample, " ".join(findings))
        self.assertEqual(set(), _privacy_findings("02:00:00:00:00:01 00:00:00:00:00:00 2001:db8::1"))
        self.assertEqual(set(), _privacy_findings('__version__ = "1.3.0.0-preview"'))
        self.assertIn("embedded-ip-address", _privacy_findings('server = "' + ".".join(map(str, [10, 20, 30, 40])) + '"'))
        self.assertEqual(set(), _privacy_findings("observer::encodeFrame(value)"))
        self.assertEqual(set(), _privacy_findings('wheel = "https://files.example.invalid/packages/ab/cd/file.whl"'))

    def test_candidate_error_suppresses_local_and_temporary_paths(self):
        for value in (
            str(Path("/") / "home" / "synthetic-user" / "private"), "/tmp/private-checkout",
            "/opt/local-install", ".".join(map(str, [10, 20, 30, 40])), "\\".join(["C:", "Users", "synthetic-user", "checkout"]),
        ):
            message = _safe_error(BridgeCandidateError("bad source " + value))
            self.assertNotIn(value, message)

    def test_scoped_overlay_applies_cleanly_and_preserves_new_upstream_files(self):
        result = prepare_bridge_candidate(
            self.base, self.latest, self.overlay,
            self.overlay / "observer/meshcoretomqtt", self.output,
            base_sha=self.base_sha,
        )
        self.assertEqual("unapproved-candidate", result["candidate_status"])
        self.assertEqual(self.latest_sha, result["latest_upstream_sha"])
        self.assertIn("UPSTREAM = True", (self.latest / "bridge/other.py").read_text())
        patch = (self.output / "observer-bridge-overlay.patch").read_text()
        self.assertNotIn("PRIVATE LOCAL DEPLOYMENT DETAIL", patch)
        self.assertNotIn("README.md", result["changed_allowlisted_files"])

    def test_report_includes_new_allowlisted_overlay_files(self):
        source = self.overlay / "observer/meshcoretomqtt/bridge/new_observer.py"
        source.write_text("OBSERVER_TRANSPORT = True\n")
        git(self.overlay, "add", "-A")
        git(self.overlay, "commit", "--quiet", "-m", "new observer source")
        result = prepare_bridge_candidate(
            self.base, self.latest, self.overlay,
            self.overlay / "observer/meshcoretomqtt", self.output,
            base_sha=self.base_sha,
        )
        self.assertIn("bridge/new_observer.py", result["changed_allowlisted_files"])

    def test_conflicting_upstream_change_reports_no_fallback(self):
        git(self.latest, "checkout", "-b", "conflict")
        (self.latest / "bridge/transport.py").write_text("OBSERVER = 99\n")
        git(self.latest, "add", "-A")
        git(self.latest, "commit", "--quiet", "-m", "conflict")
        git(self.latest, "checkout", "--quiet", "--detach")
        output = self.root / "conflict-candidate"
        with self.assertRaises(BridgeCandidateError):
            prepare_bridge_candidate(
                self.base, self.latest, self.overlay,
                self.overlay / "observer/meshcoretomqtt", output,
                base_sha=self.base_sha,
            )
        report = json.loads((output / "bridge-candidate-report.json").read_text())
        self.assertEqual("upstream-conflict-or-check-failed", report["candidate_status"])
        self.assertIn("No fallback", report["limitations"][0])

    def test_environment_specific_diff_is_rejected_without_echoing_match(self):
        source = self.overlay / "observer/meshcoretomqtt/bridge/transport.py"
        fixture_ip = ".".join(map(str, [10, 20, 30, 40]))
        source.write_text('HOST = "' + fixture_ip + '"\n')
        output = self.root / "privacy-candidate"
        with self.assertRaises(BridgeCandidateError):
            prepare_bridge_candidate(
                self.base, self.latest, self.overlay,
                self.overlay / "observer/meshcoretomqtt", output,
                base_sha=self.base_sha,
            )
        report_text = (output / "bridge-candidate-report.json").read_text()
        self.assertIn("embedded-ip-address", report_text)
        self.assertNotIn(fixture_ip, report_text)
