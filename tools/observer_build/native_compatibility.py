"""Run required native observer tests with the real v1.18 CommonCLI migration.

This harness is for the disposable, patched artifact checkout only. It writes a
temporary PlatformIO config and an RTClib header shim beside that checkout, runs
native tests only, then removes both fixtures. The upstream config and sources
are never edited by this script.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Iterator


MIGRATION_TEST = "ObserverNodePrefs.CommonCLIMigratesLegacyBinaryPrefsWithoutEnrollingObserver"
NATIVE_FLAGS = "build_flags = -std=c++17"


class NativeCompatibilityError(RuntimeError):
    """Raised when the disposable real-CommonCLI test setup is incomplete."""


@dataclass(frozen=True)
class NativeTestOverlay:
    config_path: Path
    fixture_dir: Path


def _verify_migration_fixture(source: Path) -> None:
    test_path = source / "test/test_observer_transport/test_stream_queue.cpp"
    cli_path = source / "src/helpers/CommonCLI.cpp"
    variant_ini = source / "variants/sensecap_solar/platformio.ini"
    required_source = (
        "#if defined(OBSERVER_REAL_COMMONCLI_TEST)",
        '#include "../../src/helpers/CommonCLI.cpp"',
        f"TEST({MIGRATION_TEST.split('.')[0]}, {MIGRATION_TEST.split('.')[1]})",
    )
    if not test_path.is_file() or not cli_path.is_file() or not variant_ini.is_file():
        raise NativeCompatibilityError("Source is missing the patched observer migration test inputs")
    test_text = test_path.read_text(encoding="utf-8")
    missing = [token for token in required_source if token not in test_text]
    if missing:
        raise NativeCompatibilityError(
            "Required migration fixture is missing real CommonCLI integration: " + ", ".join(missing)
        )
    if "CONFIG_MAX_KEYLEN=19" not in variant_ini.read_text(encoding="utf-8"):
        raise NativeCompatibilityError("Patched custom target is missing CONFIG_MAX_KEYLEN=19")


def _with_native_test_flags(config: str, fixture_include: str) -> str:
    """Return a config copy adding only test-only flags to [env:native]."""
    sections = list(re.finditer(r"(?m)^\[env:native\][ \t]*$", config))
    if len(sections) != 1:
        raise NativeCompatibilityError("Expected exactly one [env:native] section")
    start = sections[0].start()
    next_section = re.search(r"(?m)^\[", config[sections[0].end():])
    end = sections[0].end() + next_section.start() if next_section else len(config)
    section = config[start:end]
    if section.count(NATIVE_FLAGS) != 1:
        raise NativeCompatibilityError("Native test flags do not match the expected upstream baseline")
    if "OBSERVER_REAL_COMMONCLI_TEST" in section or "CONFIG_MAX_KEYLEN" in section:
        raise NativeCompatibilityError("Native test overlay already contains observer compatibility flags")
    flags = (
        NATIVE_FLAGS
        + "\n  -D CONFIG_MAX_KEYLEN=19"
        + "\n  -D OBSERVER_REAL_COMMONCLI_TEST=1"
        + "\n  -ffunction-sections"
        + "\n  -Wl,--gc-sections"
        + f"\n  -I {fixture_include}"
    )
    return config[:start] + section.replace(NATIVE_FLAGS, flags, 1) + config[end:]


@contextmanager
def native_test_overlay(source_dir: Path) -> Iterator[NativeTestOverlay]:
    """Create a scoped native config + RTClib shim without touching upstream files."""
    source = source_dir.resolve()
    config_path = source / "platformio.ini"
    if not config_path.is_file():
        raise NativeCompatibilityError(f"Missing PlatformIO config in patched source: {source}")
    _verify_migration_fixture(source)
    original = config_path.read_bytes()

    with tempfile.TemporaryDirectory(prefix=".observer-commoncli-test-", dir=source) as temp_name:
        fixture_dir = Path(temp_name)
        (fixture_dir / "RTClib.h").write_text(
            "#pragma once\n"
            "class DateTime {\n"
            " public:\n"
            "  explicit DateTime(unsigned long) {}\n"
            "  int hour() const { return 0; }\n"
            "  int minute() const { return 0; }\n"
            "  int day() const { return 1; }\n"
            "  int month() const { return 1; }\n"
            "  int year() const { return 1970; }\n"
            "};\n",
            encoding="utf-8",
        )
        overlay_text = _with_native_test_flags(
            original.decode("utf-8"), fixture_dir.name
        )
        descriptor, overlay_name = tempfile.mkstemp(
            prefix=".platformio-observer-native-", suffix=".ini", dir=source
        )
        overlay_path = Path(overlay_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as overlay_file:
                overlay_file.write(overlay_text)
            yield NativeTestOverlay(overlay_path, fixture_dir)
        finally:
            overlay_path.unlink(missing_ok=True)
            if config_path.read_bytes() != original:
                raise NativeCompatibilityError("Native test harness modified upstream platformio.ini")


def native_test_commands(config_path: Path) -> tuple[list[str], list[str]]:
    """Return fixed test-only commands; no firmware build or upload target."""
    config_arg = config_path.name
    return (
        ["pio", "test", "--project-conf", config_arg, "-e", "native", "-e", "native_kiss_modem", "-vv"],
        ["pio", "test", "--project-conf", config_arg, "-e", "native", "-f", "test_observer_transport", "-vv"],
    )


def require_migration_test_passed(output: str) -> None:
    """Fail closed if the conditional CommonCLI test was absent or failed."""
    if f"[ RUN      ] {MIGRATION_TEST}" not in output or f"[       OK ] {MIGRATION_TEST}" not in output:
        raise NativeCompatibilityError(f"Required native test did not pass: {MIGRATION_TEST}")
    if f"[  FAILED  ] {MIGRATION_TEST}" in output:
        raise NativeCompatibilityError(f"Required native test failed: {MIGRATION_TEST}")


def _run_test(source: Path, command: list[str], *, require_migration: bool) -> None:
    print("$ " + shlex.join(command), flush=True)
    result = subprocess.run(command, cwd=source, text=True, capture_output=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)
    if require_migration:
        require_migration_test_passed(result.stdout + result.stderr)


def run_native_compatibility_tests(source_dir: Path) -> None:
    source = source_dir.resolve()
    workspace = Path(__file__).resolve().parents[2]
    output_root = workspace / "out"
    if (
        output_root.is_symlink()
        or source.parent != output_root.resolve()
        or not (source / ".git").exists()
    ):
        raise NativeCompatibilityError(
            "Native compatibility runner only accepts a disposable Git checkout under this repository's out/"
        )
    with native_test_overlay(source) as overlay:
        for command in native_test_commands(overlay.config_path):
            # Both invocations must execute the real migration; the focused run
            # makes an accidental filter/registration omission visible by name.
            _run_test(source, command, require_migration=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="disposable exact patched artifact checkout")
    arguments = parser.parse_args()
    try:
        run_native_compatibility_tests(arguments.source)
    except (NativeCompatibilityError, OSError, subprocess.CalledProcessError) as error:
        print(f"native observer compatibility tests failed: {error}", file=sys.stderr)
        return getattr(error, "returncode", 1) or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
