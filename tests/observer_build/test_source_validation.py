import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.build import BuildError, validate_patched_observer_source
from observer_build.release_resolver import ReleasePin


class PatchedSourceValidationTests(unittest.TestCase):
    def _fixture(self, root: Path):
        source = root / "out" / "observer-source"
        paths = (
            "examples/simple_repeater/main.cpp",
            "examples/simple_repeater/MyMesh.cpp",
            "examples/simple_repeater/MyMesh.h",
            "variants/sensecap_solar/platformio.ini",
        )
        contents = {
            paths[0]: (
                "void loop() {\n"
                "  if (len >= (int)sizeof(command)) {\n"
                "    command[sizeof(command)-2] = '\\r';\n"
                "    command[sizeof(command)-1] = 0;\n"
                "  }\n"
                "  board.loop();\n"
                "}\n"
            ),
            paths[1]: (
                "void MyMesh::begin(FILESYSTEM *fs) {\n"
                "  _cli.loadPrefs(_fs);\n"
                "  board.attachDynamicPrefs(_prefs.getCustom());\n"
                "}\n"
                "\nvoid MyMesh::sendFloodScoped() {}\n"
            ),
            paths[2]: "class MyMesh {\n};\n",
            paths[3]: "[env:SenseCap_Solar]\nbuild_flags = -D STOCK=1\n",
        }
        for relative, content in contents.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=source, check=True)
        subprocess.run(["git", "add", *paths], cwd=source, check=True)
        subprocess.run(["git", "commit", "-m", "fixture stock source"], cwd=source, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "--detach"], cwd=source, check=True, capture_output=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source, check=True, text=True, capture_output=True
        ).stdout.strip()
        release = ReleasePin("repeater-v1.18.0", sha)

        (source / paths[0]).write_text(
            "void loop() {\n"
            "  observer::transport.service();\n"
            "  if (len >= (int)sizeof(command)) {\n"
            "    command[sizeof(command)-2] = '\\r';\n"
            "    command[sizeof(command)-1] = 0;\n"
            "  }\n"
            "  board.loop();\n"
            "}\n",
            encoding="utf-8",
        )
        (source / paths[1]).write_text(
            "void MyMesh::begin(FILESYSTEM *fs) {\n"
            "  _cli.loadPrefs(_fs);\n"
            "  observer::transport.attachPreferences(observerEnabled(), peer, observerHasPeer());\n"
            "  observer::transport.setPreferenceCallbacks(saveObserverEnabled, saveObserverPeer);\n"
            "  board.attachDynamicPrefs(_prefs.getCustom());\n"
            '  if (observerEnabled()) observer::transport.beginFromPreferences("SenseCAP Observer");\n'
            "}\n"
            "\nvoid MyMesh::sendFloodScoped() {}\n",
            encoding="utf-8",
        )
        (source / paths[2]).write_text(
            "class MyMesh {\n"
            "  bool observerEnabled() const { return _prefs.observer_version_ == observer::ObserverNodePrefs::kObserverVersion && _prefs.observer_enabled == 1; }\n"
            "};\n",
            encoding="utf-8",
        )
        (source / paths[3]).write_text(
            contents[paths[3]]
            + "\n[env:SenseCap_Solar_repeater_observer]\n"
            "build_flags = -D SENSECAP_OBSERVER_TRANSPORT=1 -D CONFIG_MAX_KEYLEN=19\n",
            encoding="utf-8",
        )

        patch_dir = root / "firmware" / "observer-patch-v1.18.0"
        patch_dir.mkdir(parents=True)
        for index, relative in enumerate(paths, start=1):
            (patch_dir / f"{index:04d}-fixture.patch").write_text(
                f"diff --git a/{relative} b/{relative}\n--- a/{relative}\n+++ b/{relative}\n",
                encoding="utf-8",
            )
        (patch_dir / "series.json").write_text(json.dumps({
            "schema_version": 1,
            "version": "1.18.0",
            "upstream_tag": "repeater-v1.18.0",
            "upstream_commit_sha": sha,
        }), encoding="utf-8")
        return source, patch_dir, release, paths

    def test_exact_applied_source_keeps_startup_loop_dynamic_prefs_and_stock_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, patch_dir, release, _ = self._fixture(Path(temporary))
            validate_patched_observer_source(source, patch_dir, release)

    def test_source_guard_rejects_board_loop_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, patch_dir, release, paths = self._fixture(Path(temporary))
            main = source / paths[0]
            main.write_text(main.read_text().replace("  board.loop();\n", ""), encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "board.loop"):
                validate_patched_observer_source(source, patch_dir, release)

    def test_source_guard_rejects_dynamic_preference_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, patch_dir, release, paths = self._fixture(Path(temporary))
            mesh = source / paths[1]
            mesh.write_text(mesh.read_text().replace("  board.attachDynamicPrefs(_prefs.getCustom());\n", ""), encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "observer boot preference integration"):
                validate_patched_observer_source(source, patch_dir, release)

    def test_source_guard_rejects_stock_configuration_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, patch_dir, release, paths = self._fixture(Path(temporary))
            variant = source / paths[3]
            variant.write_text(variant.read_text().replace("STOCK=1", "STOCK=2"), encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "stock config"):
                validate_patched_observer_source(source, patch_dir, release)

    def test_source_guard_rejects_boot_start_before_dynamic_prefs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, patch_dir, release, paths = self._fixture(Path(temporary))
            mesh = source / paths[1]
            text = mesh.read_text()
            startup = '  if (observerEnabled()) observer::transport.beginFromPreferences("SenseCAP Observer");\n'
            text = text.replace(startup, "").replace("  board.attachDynamicPrefs(_prefs.getCustom());\n", startup + "  board.attachDynamicPrefs(_prefs.getCustom());\n")
            mesh.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "follow loaded prefs/callbacks and dynamic prefs"):
                validate_patched_observer_source(source, patch_dir, release)


if __name__ == "__main__":
    unittest.main()
