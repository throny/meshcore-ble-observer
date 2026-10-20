import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.native_compatibility import (
    MIGRATION_TEST,
    NativeCompatibilityError,
    native_test_commands,
    native_test_overlay,
    require_migration_test_passed,
    run_native_compatibility_tests,
)


class NativeCommonCLITests(unittest.TestCase):
    def _source_fixture(self, root: Path) -> Path:
        (root / "variants/sensecap_solar").mkdir(parents=True)
        (root / "src/helpers").mkdir(parents=True)
        (root / "test/test_observer_transport").mkdir(parents=True)
        (root / "platformio.ini").write_text(
            "[platformio]\nextra_configs = variants/*\n\n"
            "[env:native]\nplatform = native\ntest_framework = googletest\n"
            "build_flags = -std=c++17\n  -I src\n\n"
            "[env:native_kiss_modem]\nplatform = native\nbuild_flags = -std=c++17\n",
            encoding="utf-8",
        )
        (root / "variants/sensecap_solar/platformio.ini").write_text(
            "[env:SenseCap_Solar_repeater_observer]\nbuild_flags = -D CONFIG_MAX_KEYLEN=19\n",
            encoding="utf-8",
        )
        (root / "src/helpers/CommonCLI.cpp").write_text(
            "// production CommonCLI implementation fixture\n", encoding="utf-8"
        )
        (root / "test/test_observer_transport/test_stream_queue.cpp").write_text(
            "#if defined(OBSERVER_REAL_COMMONCLI_TEST)\n"
            '#include "../../src/helpers/CommonCLI.cpp"\n'
            f"TEST(ObserverNodePrefs, {MIGRATION_TEST.split('.')[1]}) {{}}\n"
            "#endif\n",
            encoding="utf-8",
        )
        return root

    def test_overlay_is_test_only_and_preserves_upstream_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self._source_fixture(Path(temporary) / "source")
            original_config = (source / "platformio.ini").read_bytes()
            original_common_cli = (source / "src/helpers/CommonCLI.cpp").read_bytes()
            original_variant = (source / "variants/sensecap_solar/platformio.ini").read_bytes()

            with native_test_overlay(source) as overlay:
                self.assertTrue(overlay.config_path.is_file())
                self.assertTrue((overlay.fixture_dir / "RTClib.h").is_file())
                config = overlay.config_path.read_text(encoding="utf-8")
                native = config.split("[env:native]", 1)[1].split("[env:native_kiss_modem]", 1)[0]
                kiss = config.split("[env:native_kiss_modem]", 1)[1]
                self.assertIn("-D CONFIG_MAX_KEYLEN=19", native)
                self.assertIn("-D OBSERVER_REAL_COMMONCLI_TEST=1", native)
                self.assertIn("-ffunction-sections", native)
                self.assertIn("-Wl,--gc-sections", native)
                self.assertIn(f"-I {overlay.fixture_dir.name}", native)
                self.assertNotIn("OBSERVER_REAL_COMMONCLI_TEST", kiss)
                self.assertIn("class DateTime", (overlay.fixture_dir / "RTClib.h").read_text())
                self.assertEqual(original_config, (source / "platformio.ini").read_bytes())
                self.assertEqual(original_common_cli, (source / "src/helpers/CommonCLI.cpp").read_bytes())
                self.assertEqual(original_variant, (source / "variants/sensecap_solar/platformio.ini").read_bytes())

            self.assertFalse(overlay.config_path.exists())
            self.assertFalse(overlay.fixture_dir.exists())
            self.assertEqual(original_config, (source / "platformio.ini").read_bytes())
            self.assertEqual(original_common_cli, (source / "src/helpers/CommonCLI.cpp").read_bytes())
            self.assertEqual(original_variant, (source / "variants/sensecap_solar/platformio.ini").read_bytes())

    def test_overlay_refuses_missing_production_commoncli_migration_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self._source_fixture(Path(temporary) / "source")
            test_file = source / "test/test_observer_transport/test_stream_queue.cpp"
            test_file.write_text("TEST(ObserverNodePrefs, OtherTest) {}\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeCompatibilityError, "real CommonCLI integration"):
                with native_test_overlay(source):
                    self.fail("an incomplete migration test must not run")

    def test_native_harness_requires_named_migration_test_to_pass(self):
        passed = (
            f"[ RUN      ] {MIGRATION_TEST}\n"
            f"[       OK ] {MIGRATION_TEST} (0 ms)\n"
        )
        require_migration_test_passed(passed)
        with self.assertRaisesRegex(NativeCompatibilityError, "did not pass"):
            require_migration_test_passed("[==========] 72 tests ran\n")
        with self.assertRaisesRegex(NativeCompatibilityError, "did not pass"):
            require_migration_test_passed(f"[  FAILED  ] {MIGRATION_TEST}\n")

    def test_harness_commands_are_native_tests_only(self):
        first, focused = native_test_commands(Path(".platformio-observer-native-test.ini"))
        self.assertEqual(
            ["pio", "test", "--project-conf", ".platformio-observer-native-test.ini",
             "-e", "native", "-e", "native_kiss_modem", "-vv"],
            first,
        )
        self.assertEqual(
            ["pio", "test", "--project-conf", ".platformio-observer-native-test.ini",
             "-e", "native", "-f", "test_observer_transport", "-vv"],
            focused,
        )
        for command in (first, focused):
            self.assertNotIn("run", command)
            self.assertNotIn("upload", command)

    def test_runner_rejects_sources_outside_disposable_output_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self._source_fixture(Path(temporary) / "source")
            with patch("observer_build.native_compatibility.subprocess.run") as pio:
                pio.return_value.stdout = f"[ RUN      ] {MIGRATION_TEST}\n[       OK ] {MIGRATION_TEST}\n"
                pio.return_value.stderr = ""
                pio.return_value.returncode = 0
                with self.assertRaisesRegex(NativeCompatibilityError, "disposable.*out/"):
                    run_native_compatibility_tests(source)
                pio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
