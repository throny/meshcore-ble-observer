import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from observer_build.build import BuildError, apply_observer_patch, build_release, validate_observer_patch_scope
from observer_build.provenance import BuildManifest
from observer_build.release_resolver import ReleasePin


def _versioned_patch_fixture(root: Path, supported_sha: str) -> tuple[Path, Path, str]:
    source = root / "source"
    config = source / "src/observer/config.txt"
    config.parent.mkdir(parents=True)
    config.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    subprocess.run(["git", "add", "src/observer/config.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "--detach"], cwd=source, check=True, capture_output=True)
    source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True, text=True, capture_output=True
    ).stdout.strip()

    patch_dir = root / "observer-patch-v1.18.0"
    patch_dir.mkdir()
    (patch_dir / "0001-observer.patch").write_text(
        "diff --git a/src/observer/config.txt b/src/observer/config.txt\n"
        "--- a/src/observer/config.txt\n+++ b/src/observer/config.txt\n"
        "@@ -1,4 +1,5 @@\n alpha\n beta\n gamma\n delta\n+observer\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "version": "1.18.0",
        "upstream_tag": "repeater-v1.18.0",
        "upstream_commit_sha": supported_sha,
    }
    (patch_dir / "series.json").write_text(json.dumps(manifest), encoding="utf-8")
    return source, patch_dir, source_sha


class ProvenanceTests(unittest.TestCase):
    def test_reused_output_is_refused_before_source_or_commands_and_preserved(self):
        # Reusing compare-stock output, an old success, or obsolete extensions
        # must not leave a new invocation looking successful or delete user data.
        for names in (("baseline.hex", "observer.hex", "provenance.json"),
                      ("observer.uf2",), ("notes.txt",)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "output"
                output.mkdir()
                before = {name: b"previous bytes" for name in names}
                for name, data in before.items():
                    (output / name).write_bytes(data)
                with patch("observer_build.build._ensure_clean_detached_source") as source_check:
                    with self.assertRaisesRegex(BuildError, "fresh.*output|output.*empty"):
                        build_release(root / "source", output, release=ReleasePin("repeater-v1.17.1", "a" * 40))
                    source_check.assert_not_called()
                self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_manifest_records_reproducibility_inputs_outputs_and_memory(self):
        """Would fail if a built custom image omitted a required provenance field."""
        manifest = BuildManifest(
            release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"),
            patch_id="fixture-observer",
            patch_sha256="a" * 64,
            baseline_environment="SenseCap_Solar_repeater",
            custom_environment="SenseCap_Solar_repeater_observer",
            custom_build_flags=["-D MESH_PACKET_LOGGING=1"],
            versions={"platformio": "6.2.0", "framework-arduinoadafruitnrf52": "1.22000.0"},
            commands={"baseline": ["pio", "run", "-e", "SenseCap_Solar_repeater"], "custom": ["pio", "run", "-e", "SenseCap_Solar_repeater_observer"]},
            timestamp="2026-10-14T12:00:00Z",
            artifacts={"baseline.bin": "b" * 64, "observer.bin": "c" * 64},
            memory={"baseline": {"ram_used": 31408, "flash_used": 391340}},
        )

        payload = manifest.to_dict()
        self.assertEqual("repeater-v1.17.1", payload["release"]["tag"])
        self.assertEqual("d92964352441e53b93e8667b802e04f6e072b39e", payload["release"]["commit_sha"])
        self.assertEqual("fixture-observer", payload["patch"]["id"])
        self.assertEqual("a" * 64, payload["patch"]["sha256"])
        self.assertEqual("SenseCap_Solar_repeater_observer", payload["custom"]["environment"])
        self.assertEqual(["-D MESH_PACKET_LOGGING=1"], payload["custom"]["build_flags"])
        self.assertEqual("6.2.0", payload["versions"]["platformio"])
        self.assertEqual(["pio", "run", "-e", "SenseCap_Solar_repeater"], payload["commands"]["baseline"])
        self.assertEqual("2026-10-14T12:00:00Z", payload["timestamp"])
        self.assertEqual("c" * 64, payload["artifacts"]["observer.bin"])
        self.assertEqual(31408, payload["memory"]["baseline"]["ram_used"])

    def test_manifest_keeps_patch_bytes_and_series_manifest_identity_distinct(self):
        manifest = BuildManifest(
            release=ReleasePin("repeater-v1.18.0", "81dbae592ffad40b4212bc0b37e82b38da08f4bc"),
            patch_id="observer-patch-v1.18.0",
            patch_sha256="a" * 64,
            baseline_environment="SenseCap_Solar_repeater",
            custom_environment="SenseCap_Solar_repeater_observer",
            custom_build_flags=["-D SENSECAP_OBSERVER_TRANSPORT=1"],
            versions={"platformio": "6.2.0"},
            commands={"baseline": None, "custom": ["pio", "run"]},
            timestamp="2026-10-16T00:00:00Z",
            artifacts={"observer.hex": "b" * 64},
            memory={"baseline": None, "custom": {"ram_used": 10}},
            patch_series={
                "version": "1.18.0",
                "upstream_tag": "repeater-v1.18.0",
                "upstream_commit_sha": "81dbae592ffad40b4212bc0b37e82b38da08f4bc",
                "manifest_sha256": "c" * 64,
            },
        )

        patch = manifest.to_dict()["patch"]
        self.assertEqual("a" * 64, patch["sha256"])
        self.assertEqual("c" * 64, patch["series"]["manifest_sha256"])
        self.assertEqual("81dbae592ffad40b4212bc0b37e82b38da08f4bc", patch["series"]["upstream_commit_sha"])

    def test_patch_application_fails_closed_when_context_has_drifted(self):
        """Would fail if patch application accepted changed upstream context or fuzz."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            patches = root / "patches"
            source.mkdir()
            patches.mkdir()
            (source / "src" / "observer").mkdir(parents=True)
            (source / "src" / "observer" / "config.txt").write_text("changed-upstream-context\nsecond\nthird\nfourth\n", encoding="utf-8")
            (patches / "0001-fixture.patch").write_text(
                "--- a/src/observer/config.txt\n+++ b/src/observer/config.txt\n@@ -1,4 +1,4 @@\n-original\n second\n third\n fourth\n+observer\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BuildError, "drift"):
                apply_observer_patch(source, patches)

    def test_versioned_patch_application_rejects_source_sha_outside_series_pin(self):
        """Would fail if a reviewed series could apply to a different full upstream SHA."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            config = source / "src/observer/config.txt"
            config.parent.mkdir(parents=True)
            config.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            subprocess.run(["git", "add", "src/observer/config.txt"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "--detach"], cwd=source, check=True, capture_output=True)

            patch_dir = root / "observer-patch-v1.18.0"
            patch_dir.mkdir()
            (patch_dir / "0001-observer.patch").write_text(
                "diff --git a/src/observer/config.txt b/src/observer/config.txt\n"
                "--- a/src/observer/config.txt\n+++ b/src/observer/config.txt\n"
                "@@ -1,4 +1,5 @@\n alpha\n beta\n gamma\n delta\n+observer\n",
                encoding="utf-8",
            )
            (patch_dir / "series.json").write_text(json.dumps({
                "schema_version": 1,
                "version": "1.18.0",
                "upstream_tag": "repeater-v1.18.0",
                "upstream_commit_sha": "f" * 40,
            }), encoding="utf-8")
            before = config.read_bytes()

            with self.assertRaisesRegex(BuildError, "series.*SHA|SHA.*series"):
                apply_observer_patch(source, patch_dir)

            self.assertEqual(before, config.read_bytes())
            self.assertEqual("", subprocess.run(
                ["git", "status", "--porcelain"], cwd=source, check=True,
                text=True, capture_output=True,
            ).stdout)

    def test_versioned_series_manifest_requires_full_sha_and_exact_schema(self):
        for mutation in ("short_sha", "missing_sha", "extra_field"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, patch_dir, source_sha = _versioned_patch_fixture(root, "a" * 40)
                manifest_path = patch_dir / "series.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if mutation == "short_sha":
                    manifest["upstream_commit_sha"] = source_sha[:12]
                elif mutation == "missing_sha":
                    del manifest["upstream_commit_sha"]
                else:
                    manifest["fallback"] = "repeater-v1.17.1"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                with self.assertRaisesRegex(BuildError, "series manifest"):
                    validate_observer_patch_scope([patch_dir / "0001-observer.patch"], source)

    def test_versioned_patch_directory_without_series_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            patch_dir = Path(temporary) / "observer-patch-v1.18.0"
            patch_dir.mkdir()
            patch = patch_dir / "0001-fixture.patch"
            patch.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "missing required series.json"):
                validate_observer_patch_scope([patch])

    def test_build_rejects_latest_pin_outside_versioned_series_before_source_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, patch_dir, source_sha = _versioned_patch_fixture(root, "d" * 40)
            output = root / "artifacts"
            wrong_release = ReleasePin("repeater-v1.18.0", source_sha)
            with patch("observer_build.build._ensure_clean_detached_source") as source_check, patch(
                "observer_build.build._effective_environment"
            ) as platformio:
                with self.assertRaisesRegex(BuildError, "series.*repeater-v1.18.0"):
                    build_release(source, output, release=wrong_release, patch_dir=patch_dir)
                source_check.assert_not_called()
                platformio.assert_not_called()

    def test_dynamic_latest_rejects_historical_flat_series_before_source_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, version_dir, source_sha = _versioned_patch_fixture(root, "d" * 40)
            (version_dir / "series.json").unlink()
            patch_dir = root / "observer-patch"
            version_dir.rename(patch_dir)
            release = ReleasePin("repeater-v1.18.0", source_sha)
            with patch("observer_build.build._ensure_clean_detached_source") as source_check:
                with self.assertRaisesRegex(BuildError, "[Hh]istorical.*repeater-v1.17.1"):
                    build_release(source, root / "out", release=release, patch_dir=patch_dir)
                source_check.assert_not_called()

    def test_historical_flat_series_still_accepts_only_its_fixture_pin(self):
        from observer_build.build import validate_patch_series_release

        with tempfile.TemporaryDirectory() as temporary:
            patch_dir = Path(temporary) / "observer-patch"
            patch_dir.mkdir()
            self.assertIsNone(validate_patch_series_release(
                patch_dir,
                ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"),
            ))

    def test_matching_series_manifest_applies_only_to_its_full_source_sha(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, patch_dir, source_sha = _versioned_patch_fixture(root, "a" * 40)
            manifest_path = patch_dir / "series.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["upstream_commit_sha"] = source_sha
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            apply_observer_patch(source, patch_dir)

            self.assertEqual(
                "alpha\nbeta\ngamma\ndelta\nobserver\n",
                (source / "src/observer/config.txt").read_text(encoding="utf-8"),
            )

    def test_build_refuses_a_dirty_source_checkout_before_running_platformio(self):
        """Would fail if a build mixed unreviewed upstream changes into an artifact."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            (source / "platformio.ini").write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "add", "platformio.ini"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "--detach"], cwd=source, check=True, capture_output=True)
            (source / "platformio.ini").write_text("unreviewed-change\n", encoding="utf-8")

            with self.assertRaisesRegex(BuildError, "dirty"):
                build_release(
                    source,
                    root / "output",
                    release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"),
                    patch_dir=root / "patches",
                )

    def test_build_refuses_a_detached_checkout_that_is_not_the_resolved_commit(self):
        """Would fail if the recorded source pin differed from the code that was compiled."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            (source / "platformio.ini").write_text("[env:SenseCap_Solar_repeater]\n", encoding="utf-8")
            subprocess.run(["git", "add", "platformio.ini"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "--detach"], cwd=source, check=True, capture_output=True)
            (root / "patches").mkdir()

            with self.assertRaisesRegex(BuildError, "does not match"):
                build_release(
                    source,
                    root / "output",
                    release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"),
                    patch_dir=root / "patches",
                )

    def test_mocked_build_creates_custom_only_or_optin_stock_without_upload(self):
        for compare in (False, True):
            with self.subTest(compare_stock=compare):
                self._check_release_build(compare)

    def test_failed_build_does_not_publish_partial_artifacts_or_manifest(self):
        self._check_release_build(True, fail_versions=True)

    def _check_release_build(self, compare, fail_versions=False):
        """Would fail if the build skipped either target, provenance, or invoked flashing."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            patches = root / "patches"
            source.mkdir()
            patches.mkdir()
            if compare and not fail_versions:
                output.mkdir()  # Existing empty output is also an allowed destination.
            (source / "variants" / "sensecap_solar").mkdir(parents=True)
            (source / "platformio.ini").write_text("[platformio]\nextra_configs = variants/*/platformio.ini\n", encoding="utf-8")
            (source / "variants" / "sensecap_solar" / "platformio.ini").write_text("[env:SenseCap_Solar_repeater]\nstock = 1\n", encoding="utf-8")
            (patches / "0001-fixture.patch").write_text(
                "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n@@ -2,0 +3,3 @@\n+\n+[env:SenseCap_Solar_repeater_observer]\n+build_flags = -D MESH_PACKET_LOGGING=1\n",
                encoding="utf-8",
            )
            calls = []
            real_run = subprocess.run

            def fake_run(command, **kwargs):
                if command[0] != "pio":
                    return real_run(command, **kwargs)
                calls.append(command)
                environment = command[-1]
                binary = Path(kwargs["cwd"]) / ".pio" / "build" / environment / "firmware.hex"
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(environment.encode())
                return subprocess.CompletedProcess(command, 0, stdout="RAM: 31408\nFlash: 391340\n", stderr="")

            configs = [{"build_flags": ["-D", "STOCK=1"]}, {"build_flags": ["-D", "STOCK=1"]}, {"build_flags": ["-D", "MESH_PACKET_LOGGING=1"]}]
            with patch("observer_build.build._ensure_clean_detached_source"), patch(
                "observer_build.build._platform_versions", **({"side_effect": BuildError("injected metadata failure")} if fail_versions else {"return_value": {"platformio": "6.2.0"}})
            ), patch("observer_build.build._effective_environment", side_effect=configs), patch("observer_build.build.subprocess.run", side_effect=fake_run):
                def invoke():
                    return build_release(
                        source,
                        output,
                        release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"),
                        patch_dir=patches,
                        timestamp="2026-10-14T12:00:00Z",
                        **({"compare_stock": True} if compare else {}),
                    )
                if fail_versions:
                    with self.assertRaisesRegex(BuildError, "injected metadata failure"):
                        invoke()
                    self.assertFalse(output.exists())
                    self.assertEqual({"source", "patches"}, {p.name for p in root.iterdir()})
                    return
                manifest = invoke()

            self.assertEqual(compare, (output / "baseline.hex").is_file())
            self.assertTrue((output / "observer.hex").is_file())
            self.assertTrue((output / "provenance.json").is_file())
            self.assertEqual(2 if compare else 1, len(calls))
            payload = manifest.to_dict()
            if compare:
                self.assertEqual(calls[0], payload["stock_comparison"]["command"])
                self.assertIn("baseline.hex", payload["stock_comparison"]["artifacts"])
                self.assertEqual(31408, payload["stock_comparison"]["memory"]["ram_used"])
            else:
                self.assertIsNone(payload["stock_comparison"])
                self.assertIsNone(payload["commands"]["baseline"])
                self.assertIsNone(payload["memory"]["baseline"])
            self.assertTrue(all("upload" not in part.lower() for call in calls for part in call))
            self.assertEqual("SenseCap_Solar_repeater_observer", manifest.custom_environment)
            self.assertEqual("2026-10-14T12:00:00Z", json.loads((output / "provenance.json").read_text())["timestamp"])

    def test_platform_versions_accept_slash_package_names(self):
        """Would fail if framework/platform versions with vendor paths were omitted."""
        with patch("observer_build.build._run", side_effect=[
            subprocess.CompletedProcess([], 0, "PlatformIO Core 6.2.0", ""),
            subprocess.CompletedProcess([], 0, "framework-arduinoadafruitnrf52 @ 1.22000.0\nplatform/nordicnrf52 @ 10.7.0", ""),
        ]):
            from observer_build.build import _platform_versions
            versions = _platform_versions(Path("."))
        self.assertEqual("1.22000.0", versions["framework-arduinoadafruitnrf52"])
        self.assertEqual("10.7.0", versions["platform/nordicnrf52"])

    def test_build_refuses_patch_that_changes_stock_sensecap_variant_files(self):
        """Would fail if the observer patch changed stock SenseCAP configuration/source files."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, patches = root / "source", root / "patches"
            (source / "variants" / "sensecap_solar").mkdir(parents=True)
            patches.mkdir()
            (source / "platformio.ini").write_text("[env:SenseCap_Solar_repeater]\n", encoding="utf-8")
            (source / "variants" / "sensecap_solar" / "platformio.ini").write_text("stock\n", encoding="utf-8")
            (patches / "0001-fixture.patch").write_text(
                "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n@@ -1 +1 @@\n-stock\n+changed\n", encoding="utf-8"
            )
            calls = []
            real_run = subprocess.run
            def fake_run(command, **kwargs):
                if command[0] != "pio": return real_run(command, **kwargs)
                calls.append(command)
                binary = Path(kwargs["cwd"]) / ".pio" / "build" / command[-1] / "firmware.bin"
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(b"fixture")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            configs = [{"build_flags": ["-D", "STOCK=1"]}, {"build_flags": ["-D", "STOCK=1"]}]
            with patch("observer_build.build._ensure_clean_detached_source"), patch(
                "observer_build.build._effective_environment", side_effect=configs
            ), patch("observer_build.build.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(BuildError, "custom environment|stock SenseCAP"):
                    build_release(source, root / "output", release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"), patch_dir=patches)
            self.assertEqual([], calls)

    def test_build_refuses_patch_that_changes_unapproved_shared_source(self):
        """Would fail if a patch changed shared stock firmware outside the observer seam allowlist."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, patches = root / "source", root / "patches"
            (source / "src" / "helpers").mkdir(parents=True)
            patches.mkdir()
            (source / "platformio.ini").write_text("[env:SenseCap_Solar_repeater]\n", encoding="utf-8")
            (source / "src" / "helpers" / "Common.cpp").write_text("stock\n", encoding="utf-8")
            (patches / "0001-fixture.patch").write_text(
                "--- a/src/helpers/Common.cpp\n+++ b/src/helpers/Common.cpp\n@@ -1 +1 @@\n-stock\n+changed\n", encoding="utf-8"
            )
            self._assert_scope_rejected(source, patches, "outside the observer path allowlist")

    def test_build_accepts_observer_specific_source_path(self):
        """Would fail if a maintained observer-only patch path were rejected."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, patches, output = root / "source", root / "patches", root / "output"
            (source / "src" / "observer").mkdir(parents=True)
            patches.mkdir()
            (source / "platformio.ini").write_text("[env:SenseCap_Solar_repeater]\n", encoding="utf-8")
            (patches / "0001-fixture.patch").write_text(
                "--- /dev/null\n+++ b/src/observer/Observer.cpp\n@@ -0,0 +1 @@\n+observer\n", encoding="utf-8"
            )
            self._run_mocked_build(source, patches, output)
            self.assertTrue((output / "observer.bin").is_file())

    def test_patch_scope_rejects_unknown_paths_regardless_of_extension(self):
        """Would fail if .ino, scripts, or arbitrary extension paths bypassed the allowlist."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            patches = Path(temporary)
            for index, path in enumerate(("src/main.ino", "scripts/alter_stock.py", "src/helpers/stock.xyz")):
                patch = patches / f"{index}.patch"
                patch.write_text(f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+changed\n", encoding="utf-8")
                with self.assertRaisesRegex(BuildError, "outside the observer path allowlist"):
                    validate_observer_patch_scope([patch])

    def test_patch_scope_allows_observer_paths_and_exact_shared_seams(self):
        """Would fail if approved observer additions or exact shared seams were rejected."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            patch = Path(temporary) / "allowed.patch"
            patch.write_text(
                "--- /dev/null\n+++ b/src/observer/Observer.ino\n@@ -0,0 +1 @@\n+observer\n"
                "--- a/src/helpers/ArduinoSerialInterface.cpp\n+++ b/src/helpers/ArduinoSerialInterface.cpp\n@@ -1,3 +1,4 @@\n one\n two\n three\n+observer\n",
                encoding="utf-8",
            )
            validate_observer_patch_scope([patch])

    def test_patch_scope_allows_only_the_exact_classic_repeater_shared_seams(self):
        """Would fail if classic repeater seams needed by the observer target were rejected."""
        from observer_build.build import validate_observer_patch_scope
        paths = (
            "src/helpers/ArduinoSerialInterface.cpp",
            "src/helpers/ArduinoSerialInterface.h",
            "src/Dispatcher.cpp",
            "src/Dispatcher.h",
            "examples/simple_repeater/MyMesh.cpp",
            "examples/simple_repeater/MyMesh.h",
            "examples/simple_repeater/main.cpp",
        )
        with tempfile.TemporaryDirectory() as temporary:
            patches = Path(temporary)
            for index, path in enumerate(paths):
                patch = patches / f"{index}.patch"
                patch.write_text(
                    f"--- a/{path}\n+++ b/{path}\n@@ -1,3 +1,4 @@\n one\n two\n three\n+observer\n",
                    encoding="utf-8",
                )
            validate_observer_patch_scope(sorted(patches.glob("*.patch")))

    def test_patch_scope_allows_only_the_observer_native_transport_test(self):
        """Would fail if Task 3's exact native test were rejected or the test allowlist broadened."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "allowed.patch"
            allowed.write_text(
                "--- /dev/null\n+++ b/test/test_observer_transport/test_stream_queue.cpp\n"
                "@@ -0,0 +1 @@\n+test\n", encoding="utf-8"
            )
            validate_observer_patch_scope([allowed])
            rejected = root / "rejected.patch"
            rejected.write_text(
                "--- /dev/null\n+++ b/test/test_other/test.cpp\n@@ -0,0 +1 @@\n+test\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BuildError, "outside the observer path allowlist"):
                validate_observer_patch_scope([rejected])

    def test_patch_scope_rejects_context_free_shared_seam_hunk(self):
        """Would fail if a zero-context seam hunk could land at an unrelated line."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            patch = Path(temporary) / "bad-anchor.patch"
            patch.write_text(
                "--- a/examples/simple_repeater/MyMesh.cpp\n"
                "+++ b/examples/simple_repeater/MyMesh.cpp\n"
                "@@ -3,0 +4,1 @@\n+observer injection\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BuildError, "context|anchor"):
                validate_observer_patch_scope([patch])

    def test_patch_scope_allows_context_free_addition_to_new_observer_file(self):
        """Would fail if a newly added observer file required old-file context."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            patch = Path(temporary) / "new-observer.patch"
            patch.write_text(
                "--- /dev/null\n+++ b/src/observer/NewCodec.h\n@@ -0,0 +1,2 @@\n"
                "+#pragma once\n+\n", encoding="utf-8"
            )
            validate_observer_patch_scope([patch])

    def test_patch_scope_rejects_nearby_classic_repeater_paths(self):
        """Would fail if the classic repeater seam exception became a directory allowlist."""
        from observer_build.build import validate_observer_patch_scope
        paths = (
            "src/Dispatcher.hpp",
            "src/Packet.cpp",
            "examples/simple_repeater/Other.cpp",
            "examples/simple_repeater/subdir/main.cpp",
        )
        with tempfile.TemporaryDirectory() as temporary:
            patches = Path(temporary)
            for index, path in enumerate(paths):
                patch = patches / f"{index}.patch"
                patch.write_text(
                    f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-stock\n+observer\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(BuildError, "outside the observer path allowlist"):
                    validate_observer_patch_scope([patch])

    def test_patch_scope_allows_custom_platformio_registration(self):
        """Would fail if the narrowly permitted custom target registration were rejected."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            patch = Path(temporary) / "custom-platformio.patch"
            patch.write_text(
                "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n@@ -4,0 +5,3 @@\n+\n+[env:SenseCap_Solar_repeater_observer]\n+build_flags = -D MESH_PACKET_LOGGING=1\n",
                encoding="utf-8",
            )
            validate_observer_patch_scope([patch])

    def test_variant_platformio_patch_rejects_non_additive_or_script_changes(self):
        """Would fail if a custom-target patch could edit stock/shared config or add scripts."""
        from observer_build.build import validate_observer_patch_scope
        fixtures = {
            "stock": "@@ -1 +1 @@\n-[env:SenseCap_Solar_repeater]\n+[env:SenseCap_Solar_repeater]\n",
            "shared": "@@ -1 +1 @@\n-build_flags = -D SHARED=1\n+build_flags = -D SHARED=2\n",
            "delete": "@@ -1 +0,0 @@\n-[env:shared]\n",
            "script": "@@ -1,0 +2,3 @@\n+\n+[env:SenseCap_Solar_repeater_observer]\n+extra_scripts = scripts/observer.py\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            for name, hunk in fixtures.items():
                patch = Path(temporary) / f"{name}.patch"
                patch.write_text(
                    "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n" + hunk,
                    encoding="utf-8",
                )
                with self.assertRaises(BuildError, msg=name):
                    validate_observer_patch_scope([patch])

    def test_variant_platformio_patch_allows_only_one_observer_section_at_eof(self):
        """Would fail if a config hunk could add another section or be placed before EOF."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variant = root / "variants" / "sensecap_solar"
            variant.mkdir(parents=True)
            target = variant / "platformio.ini"
            target.write_text("[env:stock]\nflag = 1\n[env:later]\nflag = 2\n", encoding="utf-8")
            invalid = {
                "extra-target": "+\n+[env:SenseCap_Solar_repeater_observer]\n+flag = 1\n+[env:unreviewed_extra_target]\nflag = 2\n",
                "inherited": "+\n+[env:some_base]\nflag = 1\n+[env:SenseCap_Solar_repeater_observer]\nflag = 2\n",
                "not-eof": "+\n+[env:SenseCap_Solar_repeater_observer]\nflag = 1\n",
            }
            for name, additions in invalid.items():
                patch = root / f"{name}.patch"
                placement = "@@ -2,0 +3,3 @@" if name == "not-eof" else "@@ -4,0 +5,3 @@"
                patch.write_text(
                    "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n" + placement + "\n" + additions,
                    encoding="utf-8",
                )
                with self.assertRaises(BuildError, msg=name):
                    validate_observer_patch_scope([patch], root)
            valid = root / "valid.patch"
            valid.write_text(
                "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n@@ -4,0 +5,4 @@\n+\n+[env:SenseCap_Solar_repeater_observer]\n+extends = SenseCap_Solar_repeater\n+build_flags = -D MESH_PACKET_LOGGING=1\n",
                encoding="utf-8",
            )
            validate_observer_patch_scope([valid], root)

    def test_variant_platformio_patch_rejects_whitespace_and_commented_extra_headers(self):
        """Would fail if INI-normalized extra section headers bypassed raw-header validation."""
        from observer_build.build import validate_observer_patch_scope
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variant = root / "variants" / "sensecap_solar"
            variant.mkdir(parents=True)
            (variant / "platformio.ini").write_text("[env:stock]\nflag = 1\n", encoding="utf-8")
            for name, extra in {
                "spaced": " [env:observer_base]\nflag = 1\n",
                "commented": "[env:observer_base] ; inherited target\nflag = 1\n",
            }.items():
                patch = root / f"{name}.patch"
                patch.write_text(
                    "--- a/variants/sensecap_solar/platformio.ini\n+++ b/variants/sensecap_solar/platformio.ini\n@@ -2,0 +3,5 @@\n+\n+[env:SenseCap_Solar_repeater_observer]\n+flag = 1\n" + "\n".join(f"+{line}" for line in extra.splitlines()) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(BuildError, msg=name):
                    validate_observer_patch_scope([patch], root)

    def _assert_scope_rejected(self, source, patches, message):
        with patch("observer_build.build._ensure_clean_detached_source"), patch(
            "observer_build.build._effective_environment", side_effect=[{"build_flags": ["-D", "STOCK=1"]}, {"build_flags": ["-D", "STOCK=1"]}]
        ), patch("observer_build.build._run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch(
            "observer_build.build._copy_artifacts", return_value={"observer.bin": "a" * 64}
        ):
            with self.assertRaisesRegex(BuildError, message):
                build_release(source, source / "output", release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"), patch_dir=patches)

    def _run_mocked_build(self, source, patches, output):
        calls = []
        def fake_run(command, *, cwd):
            calls.append(command)
            artifact = cwd / ".pio" / "build" / command[-1] / "firmware.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"fixture")
            return subprocess.CompletedProcess(command, 0, "", "")
        configs = [{"build_flags": ["-D", "STOCK=1"]}, {"build_flags": ["-D", "STOCK=1"]}, {"build_flags": ["-D", "MESH_PACKET_LOGGING=1"]}]
        with patch("observer_build.build._ensure_clean_detached_source"), patch(
            "observer_build.build._effective_environment", side_effect=configs
        ), patch("observer_build.build._run", side_effect=fake_run), patch(
            "observer_build.build._platform_versions", return_value={"platformio": "6.2.0"}
        ):
            build_release(source, output, release=ReleasePin("repeater-v1.17.1", "d92964352441e53b93e8667b802e04f6e072b39e"), patch_dir=patches)
