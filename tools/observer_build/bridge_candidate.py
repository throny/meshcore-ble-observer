"""Build an upstream bridge overlay candidate from the recorded clean base."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


BRIDGE_REPO = "https://github.com/Cisien/meshcoretomqtt.git"
BRIDGE_BASE_SHA = "9b4e022e298020ef3359b36d8c364931de058af5"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ALLOW_PREFIXES = ("bridge/", "installer/", "nix/", "tests/", "scripts/", "systemd/", "presets/")
_ALLOW_FILES = frozenset({
    ".gitignore", "Dockerfile", "LICENSE", "auth_token.py", "flake.nix",
    "config.ble-preflight.example.toml", "config.toml.example",
    "config_loader.py", "install.sh", "mctomqtt.py", "mctomqtt.service",
    "preflight_config.py", "pyproject.toml", "uv.lock",
})
_HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users|root)/[^/\s:'\"]+")
_WINDOWS_HOME = re.compile(r"(?i)[A-Z]:(?:\\\\|\\)Users(?:\\\\|\\)[^\s\"'\\]+")
_SAFE_WINDOWS_TEST_PATHS = frozenset({r"c:\users\foo", r"c:\\users\\foo"})


def _test_summary(text: str) -> str:
    """Retain only aggregate pytest counts; omit test IDs, values and paths."""
    counts = []
    for count, label in re.findall(r"\b([0-9]+) (passed|failed|error|errors|skipped|xfailed|xpassed)\b", text):
        counts.append(f"{count} {label}")
    return ", ".join(counts[-8:]) if counts else "summary unavailable"
_TEMPORARY_ERROR_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:tmp|var/tmp|workspace)/[^\s:'\"]+")
_ABSOLUTE_ERROR_PATH = re.compile(r"(?<![A-Za-z0-9:])/(?:[^\s:'\"]+)")
_MAC = re.compile(r"(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
_IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
_IPV6 = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![A-Za-z0-9_])")
_DEVICE_NAME_SHAPE = re.compile(r"\b[A-Z]{2}-[A-Z]{2}-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b")
_CUSTOM_INSTALL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:opt|etc|run)/([A-Za-z0-9_.-]+)")
_LOCAL_SERVICE_COMPONENT = re.compile(r"(?i)(?:^|[._-])observer(?:$|[._-])")
_SYNTHETIC_MAC_FIXTURES = frozenset({
    "02:00:00:00:00:01",  # deterministic locally administered unit-test peer
    "00:00:00:00:00:00",  # invalid-address validator fixture
    "ff:ff:ff:ff:ff:ff",  # broadcast-address validator fixture
})
_SAFE_IPV4_FIXTURES = frozenset({
    "0.0.0.0", "127.0.0.1", "255.255.255.255",
    "192.0.2.1", "198.51.100.1", "203.0.113.1",
})
_SAFE_IPV6_FIXTURES = frozenset({"::", "::1", "2001:db8::1"})
class BridgeCandidateError(ValueError):
    """Bridge update failed without altering either upstream checkout."""


def _privacy_findings(text: str) -> set[str]:
    """Return finding categories only; never include matched values in reports."""
    findings = set()
    if _HOME_PATH.search(text):
        findings.add("operator-home-path")
    if any(match.group().casefold() not in _SAFE_WINDOWS_TEST_PATHS
           for match in _WINDOWS_HOME.finditer(text)):
        findings.add("operator-home-path")
    if _DEVICE_NAME_SHAPE.search(text):
        findings.add("operator-device-identity")
    for match in _CUSTOM_INSTALL_PATH.finditer(text):
        if _LOCAL_SERVICE_COMPONENT.search(match.group(1)):
            findings.add("deployment-specific-os-or-path")
    for match in _IPV4.finditer(text):
        value = match.group()
        if value in _SAFE_IPV4_FIXTURES:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        source_line = text[line_start:line_end]
        if re.search(r"(?i)(?:__version__|\bversion\b|client_version|meshcoretomqtt/)", source_line):
            continue
        try:
            ipaddress.IPv4Address(value)
            findings.add("embedded-ip-address")
        except ValueError:
            findings.add("malformed-ip-address")
    for match in _IPV6.finditer(text):
        value = match.group()
        if value.startswith("::"):
            before = text[match.start() - 1] if match.start() else ""
            after = text[match.end()] if match.end() < len(text) else ""
            if before.isalnum() and (after.isalnum() or after == "_"):
                # C++ qualified names such as observer::encodeFrame are not
                # address literals, even when an identifier begins with a-f.
                continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        source_line = text[line_start:line_end]
        # Colons in package URLs and path/hash fragments are not IPv6. Bracketed
        # address literals remain checked even when used as a URL authority.
        if "://" in source_line and not re.search(r"\[[0-9a-f:]+\]", source_line, re.I):
            continue
        if value in _SAFE_IPV6_FIXTURES:
            continue
        try:
            ipaddress.IPv6Address(value)
            if value not in _SAFE_IPV6_FIXTURES:
                findings.add("embedded-ip-address")
        except ValueError:
            continue
    if any(match.group().lower() not in _SYNTHETIC_MAC_FIXTURES for match in _MAC.finditer(text)):
        findings.add("non-placeholder-ble-address")
    return findings


def _git(root: Path, *args: str, capture=True):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, text=True,
            capture_output=capture,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BridgeCandidateError("Git upstream candidate operation failed; inspect the action step for details") from error


def _head(root: Path) -> str:
    result = _git(root, "rev-parse", "HEAD").stdout.strip().lower()
    if not _SHA.fullmatch(result):
        raise BridgeCandidateError("Upstream checkout did not resolve to a full immutable commit")
    if _git(root, "status", "--porcelain").stdout.strip():
        raise BridgeCandidateError("Upstream checkouts must be clean before overlay generation")
    symbolic = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True)
    if symbolic.returncode == 0:
        raise BridgeCandidateError("Upstream checkout must be detached at its immutable commit")
    return result


def _included(path: str) -> bool:
    return path in _ALLOW_FILES or path.startswith(_ALLOW_PREFIXES)


def _tracked_overlay_files(repository: Path, package_dir: Path) -> dict[str, Path]:
    root = repository.resolve()
    package = package_dir.resolve()
    try:
        prefix = package.relative_to(root).as_posix().rstrip("/") + "/"
    except ValueError:
        raise BridgeCandidateError("Bridge overlay must be inside the current Git worktree") from None
    listing = _git(root, "ls-files", "-z", "--", prefix).stdout
    found = {}
    for item in listing.split("\0"):
        if not item:
            continue
        rel = item[len(prefix):]
        if not _included(rel):
            continue
        source = root / item
        if source.is_symlink() or not source.is_file():
            raise BridgeCandidateError("Public bridge overlay accepts tracked regular files only")
        found[rel] = source
    if not found:
        raise BridgeCandidateError("No allowlisted bridge source files were found")
    return found


def _safe_error(error: Exception) -> str:
    """Keep useful candidate cause while suppressing command/path/value spill."""
    if isinstance(error, BridgeCandidateError):
        text = str(error)
        if (_privacy_findings(text) or _TEMPORARY_ERROR_PATH.search(text)
                or _ABSOLUTE_ERROR_PATH.search(text) or _WINDOWS_HOME.search(text)):
            return "Bridge candidate validation failed; sensitive path/value detail suppressed"
        return text[:2000]
    if isinstance(error, subprocess.CalledProcessError):
        return f"Upstream command failed (exit {error.returncode}); command details suppressed"
    if isinstance(error, OSError):
        return f"Filesystem/process operation failed ({error.__class__.__name__}); path details suppressed"
    return f"Candidate operation failed ({error.__class__.__name__}); details suppressed"


def _report(report_path: Path, payload: dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_bridge_candidate(
    base_repo: Path,
    latest_repo: Path,
    overlay_repository: Path,
    overlay_package_dir: Path,
    output_dir: Path,
    *,
    base_sha: str = BRIDGE_BASE_SHA,
    run_tests: bool = False,
    test_python: Path | None = None,
    firmware_source: Path | None = None,
) -> dict[str, object]:
    """Derive a scoped patch from the exact recorded upstream base and test on a new SHA.

    The caller supplies local checkouts, making this fully testable with local Git
    fixtures and keeping network access out of unit tests.
    """
    base_repo = Path(base_repo).resolve()
    latest_repo = Path(latest_repo).resolve()
    output_dir = Path(output_dir).absolute()
    report_path = output_dir / "bridge-candidate-report.json"
    if output_dir.is_symlink() or (output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir()))):
        raise BridgeCandidateError("Bridge candidate output must be absent or an empty real directory")
    if not _SHA.fullmatch(base_sha):
        raise BridgeCandidateError("Bridge baseline must be an exact full commit SHA")
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "schema_version": 1,
        "upstream_repository": BRIDGE_REPO,
        "recorded_base_sha": base_sha,
        "candidate_status": "preparing",
        "overlay_patch_sha256": None,
        "latest_upstream_sha": None,
        "tests": "not-run",
    }
    try:
        base_head = _head(base_repo)
        latest_head = _head(latest_repo)
        if base_head != base_sha:
            raise BridgeCandidateError("Recorded bridge baseline checkout SHA does not match the expected source pin")
        result["latest_upstream_sha"] = latest_head
        source_files = _tracked_overlay_files(Path(overlay_repository), Path(overlay_package_dir))
        with tempfile.TemporaryDirectory(prefix="meshcoretomqtt-base-") as temporary:
            overlay = Path(temporary) / "overlay"
            subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(base_repo), str(overlay)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(overlay), "checkout", "--quiet", "--detach", base_sha], check=True, capture_output=True, text=True)
            # Absence in the local snapshot is not a deletion instruction: keep
            # upstream files unless a future reviewed deletion is explicit.
            for rel, source in source_files.items():
                target = overlay / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            _git(overlay, "add", "-A", "--", ".")
            patch = _git(overlay, "diff", "--cached", "--binary", "HEAD").stdout
            if not patch:
                raise BridgeCandidateError("The allowlisted bridge overlay contains no changes from its recorded upstream base")
            changed_paths = _git(overlay, "diff", "--cached", "--name-only", "HEAD").stdout.splitlines()
            privacy_findings = []
            for rel in changed_paths:
                file_diff = _git(overlay, "diff", "--cached", "--binary", "HEAD", "--", rel).stdout
                findings = _privacy_findings(file_diff)
                if findings:
                    privacy_findings.append(f"{rel} [{', '.join(sorted(findings))}]")
            if privacy_findings:
                raise BridgeCandidateError(
                    "Bridge source export failed privacy checks in allowlisted files: "
                    + "; ".join(privacy_findings)
                )
        patch_bytes = patch.encode("utf-8")
        (output_dir / "observer-bridge-overlay.patch").write_bytes(patch_bytes)
        patch_sha = hashlib.sha256(patch_bytes).hexdigest()
        result["overlay_patch_sha256"] = patch_sha
        with tempfile.TemporaryDirectory(prefix="meshcoretomqtt-latest-") as temporary:
            candidate = Path(temporary) / "candidate"
            subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(latest_repo), str(candidate)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(candidate), "checkout", "--quiet", "--detach", "HEAD"], check=True, capture_output=True, text=True)
            candidate_head = _head(candidate)
            if candidate_head != latest_head:
                raise BridgeCandidateError("Latest bridge checkout changed during candidate preparation")
            (output_dir / "latest-bridge-commit.txt").write_text(latest_head + "\n", encoding="ascii")
            apply = subprocess.run(["git", "-C", str(candidate), "apply", "--check", "--verbose", str(output_dir / "observer-bridge-overlay.patch")], capture_output=True, text=True)
            if apply.returncode:
                raise BridgeCandidateError("Observer overlay conflicts with current upstream bridge; a reviewed port is required")
            subprocess.run(["git", "-C", str(candidate), "apply", "--verbose", str(output_dir / "observer-bridge-overlay.patch")], check=True, capture_output=True, text=True)
            changed = sorted(set(_git(candidate, "diff", "--name-only").stdout.splitlines())
                             | set(_git(candidate, "ls-files", "--others", "--exclude-standard").stdout.splitlines()))
            if any(not _included(path) for path in changed):
                raise BridgeCandidateError("Overlay changed files outside the public bridge allowlist")
            if run_tests:
                test_env = os.environ.copy()
                test_env.update({
                    "MCTOMQTT_SKIP_NETWORK": "1",
                    "MCTOMQTT_SKIP_SYSTEM": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                })
                if firmware_source is not None:
                    firmware_root = Path(firmware_source).resolve()
                    if not firmware_root.is_dir() or not (firmware_root / "src").is_dir():
                        raise BridgeCandidateError("Resolved firmware candidate source is unavailable for cross-layer tests")
                    test_env["OBSERVER_SOURCE"] = str(firmware_root)
                sync = subprocess.run(["uv", "sync", "--system-certs", "--frozen", "--extra", "test"], cwd=candidate, capture_output=True, text=True, env=test_env)
                if sync.returncode:
                    raise BridgeCandidateError("Current upstream bridge dependencies/lock are incompatible with the reviewed candidate")
                tests = subprocess.run(["uv", "run", "--system-certs", "--frozen", "--extra", "test", "python", "-m", "pytest", "-q", "tests/"], cwd=candidate, capture_output=True, text=True, env=test_env)
                if tests.returncode:
                    result["tests"] = "failed"
                    result["test_summary"] = _test_summary(tests.stdout + "\n" + tests.stderr)
                    raise BridgeCandidateError("Offline bridge regression suite failed on current upstream source")
                result["tests"] = "passed"
                result["test_summary"] = _test_summary(tests.stdout + "\n" + tests.stderr)
                result["test_dependency_mode"] = "frozen-lock-sync"
            elif test_python is not None:
                test_env = os.environ.copy()
                test_env.update({
                    "MCTOMQTT_SKIP_NETWORK": "1",
                    "MCTOMQTT_SKIP_SYSTEM": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                })
                if firmware_source is not None:
                    firmware_root = Path(firmware_source).resolve()
                    if not firmware_root.is_dir() or not (firmware_root / "src").is_dir():
                        raise BridgeCandidateError("Resolved firmware candidate source is unavailable for cross-layer tests")
                    test_env["OBSERVER_SOURCE"] = str(firmware_root)
                tests = subprocess.run(
                    [str(Path(test_python)), "-m", "pytest", "-q", "tests/"],
                    cwd=candidate, capture_output=True, text=True, env=test_env,
                )
                if tests.returncode:
                    result["tests"] = "failed"
                    result["test_summary"] = _test_summary(tests.stdout + "\n" + tests.stderr)
                    raise BridgeCandidateError("Offline bridge regression suite failed using the existing interpreter")
                result["tests"] = "passed"
                result["test_summary"] = _test_summary(tests.stdout + "\n" + tests.stderr)
                result["test_dependency_mode"] = "existing-interpreter-no-install"
        result["candidate_status"] = "unapproved-candidate"
        result["changed_allowlisted_files"] = changed
        result["limitations"] = [
            "Only allowlisted source, configuration examples, packaging, and tests are exported; local operator docs are excluded.",
            "No release, installation, service start, production configuration, or broker connection is performed.",
        ]
    except Exception as error:
        message = _safe_error(error)
        result["candidate_status"] = "upstream-conflict-or-check-failed"
        result["error"] = message[:2000]
        result["limitations"] = ["No fallback source or previous bridge release was substituted."]
        _report(report_path, result)
        raise BridgeCandidateError(message) from error
    _report(report_path, result)
    return result
