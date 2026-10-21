import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import hashlib
import runpy
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from observer_build.build import _effective_environment, _memory_report

ROOT = Path(__file__).resolve().parents[2]


class ReleaseCITests(unittest.TestCase):
    def test_frozen_host_lock_is_tracked_and_matches_package_metadata(self):
        import tomllib
        lock_path = "observer/meshcoretomqtt/uv.lock"
        tracked = subprocess.check_output(["git", "ls-files", "--", lock_path], cwd=ROOT, text=True)
        self.assertEqual(lock_path, tracked.strip(), "frozen CI must work from tracked files alone")
        lock = tomllib.loads((ROOT / lock_path).read_text())
        project = tomllib.loads((ROOT / "observer/meshcoretomqtt/pyproject.toml").read_text())["project"]
        package = next(p for p in lock["package"] if p["name"] == project["name"])
        # Current preview spelling normalizes to PEP 440 rc0 in uv metadata.
        self.assertEqual(project["version"].removesuffix("-preview") + "rc0", package["version"])
        self.assertEqual(project["requires-python"], lock["requires-python"])
        self.assertEqual({"bleak", "ed25519-orlp", "paho-mqtt", "pyserial", "tomli"},
                         {p["name"] for p in package["dependencies"]})
        self.assertEqual([{"name": "pytest"}], package["optional-dependencies"]["test"])

    def test_cli_refuses_reused_output_before_network_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            (output / "provenance.json").write_text("previous success")
            with patch.object(sys, "argv", ["observer_build.py", "source", str(output), "--patch-dir", "patches"]), patch(
                "observer_build.release_resolver.resolve_official_latest_repeater_release"
            ) as resolver, patch("sys.stderr", new_callable=io.StringIO) as stderr:
                with self.assertRaises(SystemExit) as exited:
                    runpy.run_path(str(ROOT / "tools/observer_build.py"), run_name="__main__")
            self.assertEqual(2, exited.exception.code)
            self.assertIn("fresh", stderr.getvalue())
            resolver.assert_not_called()

    def test_workflow_structure_when_existing_yaml_parser_is_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("optional structural check requires an existing PyYAML installation; no new dependency")
        workflow = yaml.load((ROOT / ".github/workflows/observer-ble.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual({"pull_request", "push", "workflow_dispatch", "schedule"}, set(workflow["on"]))
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        steps = workflow["jobs"]["validate"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual("false", checkout["with"]["persist-credentials"])
        build_index = next(i for i, s in enumerate(steps) if "python tools/observer_build_candidate.py" in s.get("run", ""))
        source_index = next(i for i, s in enumerate(steps) if "resolve_official_latest_repeater_release()" in s.get("run", ""))
        native_index = build_index
        artifact_index = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertLess(source_index, build_index)
        self.assertEqual(build_index, native_index)
        self.assertLess(native_index, artifact_index)
        self.assertIn("exact-source native tests", steps[native_index]["name"])
        self.assertEqual("7", steps[artifact_index]["with"]["retention-days"])
        self.assertEqual("warn", steps[artifact_index]["with"]["if-no-files-found"])
        self.assertIn("candidate", steps[artifact_index]["with"]["name"])

    def test_cli_forwards_default_and_optin(self):
        for options, expected in (([], False), (["--compare-stock"], True)):
            with patch.object(sys, "argv", ["observer_build.py", "source", "output", "--patch-dir", "patches", *options]), patch(
                "observer_build.release_resolver.resolve_official_latest_repeater_release", return_value=object()
            ), patch("observer_build.build.build_release", return_value=SimpleNamespace(artifacts={})) as build, redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    runpy.run_path(str(ROOT / "tools/observer_build.py"), run_name="__main__")
            self.assertEqual(0, exited.exception.code)
            self.assertIs(expected, build.call_args.kwargs["compare_stock"])

    def test_collects_actual_nrf52_artifacts_and_hashes_without_a_bin(self):
        from observer_build.build import _copy_artifacts, BuildError
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / ".pio/build/target"
            artifacts.mkdir(parents=True)
            output = root / "output"
            output.mkdir()
            with self.assertRaises(BuildError):
                _copy_artifacts(root, "target", output, "observer")
            expected = {}
            for suffix in ("hex", "zip", "uf2"):
                data = suffix.encode()
                (artifacts / f"firmware.{suffix}").write_bytes(data)
                expected[f"observer.{suffix}"] = hashlib.sha256(data).hexdigest()
            self.assertEqual(expected, _copy_artifacts(root, "target", output, "observer"))
            self.assertEqual(set(expected), {path.name for path in output.iterdir()})

    def test_real_platformio_config_shape_and_supported_cli(self):
        # PlatformIO config is a list of sections/pairs, not an env-specific dict.
        with patch("observer_build.build._run", return_value=subprocess.CompletedProcess([], 0,
                   '[["env:target", [["build_flags", ["-D", "TEST=1"]]]]]', "")) as run:
            self.assertEqual(["-D", "TEST=1"], _effective_environment(ROOT, "target")["build_flags"])
        self.assertEqual(["pio", "project", "config", "--json-output"], run.call_args.args[0])

    def test_real_platformio_memory_format(self):
        self.assertEqual({"ram_used": 32356, "ram_total": 235520, "flash_used": 407680,
                          "flash_total": 811008}, _memory_report(
            "RAM: [= ] 13.7% (used 32356 bytes from 235520 bytes)\n"
            "Flash: [== ] 50.3% (used 407680 bytes from 811008 bytes)"))

    def test_real_flags_can_be_one_define_per_list_entry(self):
        from observer_build.build import _logging_enabled
        self.assertTrue(_logging_enabled(["-w -DNDEBUG", "-D MESH_PACKET_LOGGING=1"]))
        self.assertTrue(_logging_enabled(["-DMESH_PACKET_LOGGING=1"]))
        self.assertFalse(_logging_enabled(["-D MESH_PACKET_LOGGING=0"]))

    def test_package_tree_versions_are_captured(self):
        from observer_build.build import _platform_versions
        with patch("observer_build.build._run", side_effect=[
            subprocess.CompletedProcess([], 0, "PlatformIO Core, version 6.2.0", ""),
            subprocess.CompletedProcess([], 0, "Platform nordicnrf52 @ 10.10.0\n├── toolchain-gccarmnoneeabi @ 1.70201.0\n└── framework-arduinoadafruitnrf52 @ 1.10700.0+sha.d541301", "")]) as run:
            versions = _platform_versions(ROOT)
        self.assertEqual("1.70201.0", versions["toolchain-gccarmnoneeabi"])
        self.assertEqual(["pio", "pkg", "list", "-e", "SenseCap_Solar_repeater_observer"], run.call_args.args[0])

    def test_cli_optin_is_explicit(self):
        cli = (ROOT / "tools/observer_build.py").read_text()
        self.assertIn('"--compare-stock"', cli)
        self.assertIn("compare_stock=arguments.compare_stock", cli)
        self.assertNotIn("manifest.artifacts['observer.bin']", cli)

    def test_workflow_artifact_only_safe_defaults(self):
        workflow = (ROOT / ".github/workflows/observer-ble.yml").read_text()
        for required in ("persist-credentials: false", "contents: read", "retention-days: 7",
                         "upload-artifact@", "native_kiss_modem", "pytest -q tests/observer_build/",
                         "MCTOMQTT_SKIP_NETWORK", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
                         "--system-certs", "--frozen", "provenance.json",
                         "GITHUB_TOKEN: ${{ github.token }}"):
            self.assertIn(required, workflow)
        self.assertIn("cron: '23 7 * * *'", workflow)
        self.assertIn("unapproved firmware candidate", workflow)
        for forbidden in ("pull_request_target", "workflow_run", "secrets.", "--compare-stock",
                          "gh release", "git push", "--insecure", "verify=False", "-t upload"):
            self.assertNotIn(forbidden, workflow)

    def test_workflow_selects_reviewed_series_for_temporary_latest_candidate(self):
        import json

        workflow = (ROOT / ".github/workflows/observer-ble.yml").read_text()
        selected_patch = "firmware/observer-lines544-release/observer-patch-v1.18.0"
        self.assertIn(f'--patch-dir "$GITHUB_WORKSPACE/{selected_patch}"', workflow)
        self.assertNotIn('--patch-dir "$GITHUB_WORKSPACE/firmware/observer-patch-v1.18.0"', workflow)
        self.assertEqual(1, workflow.count(selected_patch))
        manifest = json.loads((ROOT / selected_patch / "series.json").read_text())
        self.assertEqual("repeater-v1.18.0", manifest["upstream_tag"])
        self.assertEqual("81dbae592ffad40b4212bc0b37e82b38da08f4bc", manifest["upstream_commit_sha"])
        self.assertIn("resolve_official_latest_repeater_release()", workflow)
        self.assertIn("out/observer-resolved-release.json", workflow)
        self.assertIn("out/observer-candidate/candidate-report.json", workflow)
        self.assertIn("steps.firmware_candidate.outcome == 'failure'", workflow)
        self.assertIn("steps.bridge_candidate.outcome == 'failure'", workflow)
        self.assertIn("retention-days: 7", workflow)
        self.assertNotIn("repeater-v1.18.0", workflow)

    def test_exact_source_ci_requires_real_commoncli_migration_coverage(self):
        harness = (ROOT / "tools/observer_build/native_compatibility.py").read_text()
        self.assertIn("OBSERVER_REAL_COMMONCLI_TEST=1", harness)
        self.assertIn("RTClib.h", harness)
        self.assertIn("require_migration_test_passed", harness)

        # Test the canonical series, not a historical development checkout that
        # is deliberately absent from the clean public distribution.
        source = "\n".join(
            line[1:] for patch in sorted((ROOT / "firmware/observer-lines544-release/observer-patch-v1.18.0").glob("*.patch"))
            for line in patch.read_text().splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("#if defined(OBSERVER_REAL_COMMONCLI_TEST)", source)
        self.assertIn('#include "../../src/helpers/CommonCLI.cpp"', source)
        self.assertIn(
            "TEST(ObserverNodePrefs, CommonCLIMigratesLegacyBinaryPrefsWithoutEnrollingObserver)",
            source,
        )
        self.assertIn("run_native_compatibility_tests(source_root)", (ROOT / "tools/observer_build/candidate.py").read_text())

    def test_release_pin_and_applied_source_are_checked_before_artifact_upload(self):
        workflow = (ROOT / ".github/workflows/observer-ble.yml").read_text()
        resolved = workflow.index("resolve_official_latest_repeater_release()")
        fetch = workflow.index("'fetch', '--depth=1'")
        build = workflow.index("python tools/observer_build_candidate.py")
        artifact = workflow.index("actions/upload-artifact@")
        self.assertLess(resolved, fetch)
        self.assertLess(fetch, build)
        self.assertLess(build, artifact)
        candidate = (ROOT / "tools/observer_build/candidate.py").read_text()
        self.assertIn("validate_patch_series_release(selected_patch_dir, release)", (ROOT / "tools/observer_build/build.py").read_text())
        self.assertIn("run_native_compatibility_tests(source_root)", candidate)
        self.assertIn("candidate_manifest_sha256", candidate)
        self.assertIn("reviewed_manifest_sha256", candidate)

    def test_workflow_tracks_both_upstreams_and_excludes_private_docs_from_bridge_patch(self):
        workflow = (ROOT / ".github/workflows/observer-ble.yml").read_text()
        bridge = (ROOT / "tools/observer_build/bridge_candidate.py").read_text()
        for required in (
            "https://github.com/Cisien/meshcoretomqtt.git",
            "9b4e022e298020ef3359b36d8c364931de058af5",
            "out/bridge-latest",
            "tools/observer_bridge_candidate.py",
            "--run-tests",
            "observer-bridge-overlay.patch",
        ):
            self.assertIn(required, workflow)
        self.assertIn("tests/", bridge)
        self.assertNotIn('"README.md"', bridge)
        self.assertIn("candidate_status", bridge)
        self.assertIn("if: hashFiles('platformio.ini') != ''", workflow)
        self.assertIn("test_p1pro_public_backup.py", workflow)
        self.assertIn("test_p1pro_usb_common.py", workflow)

    def test_host_wire_suite_runs_against_the_exact_patched_firmware(self):
        workflow = (ROOT / ".github/workflows/observer-ble.yml").read_text()
        self.assertGreater(workflow.index("name: Offline host suite"),
                           workflow.index("name: Prepare strict temporary firmware candidate"))
        self.assertIn("OBSERVER_SOURCE: ${{ github.workspace }}/out/observer-source", workflow)
