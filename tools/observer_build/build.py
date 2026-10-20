"""Strict, local-only orchestration for baseline and observer firmware builds."""

from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .provenance import BuildManifest
from .release_resolver import ReleasePin


BASELINE_ENVIRONMENT = "SenseCap_Solar_repeater"
CUSTOM_ENVIRONMENT = "SenseCap_Solar_repeater_observer"

# Task 2 may add observer-only implementation under these roots and may touch
# these exact serial/logger and classic-repeater integration seams. Every other
# stock compile-relevant path is immutable across the patch application.
_OBSERVER_PATH_PREFIXES = ("src/observer/", "include/observer/", "variants/sensecap_solar/observer/")
_OBSERVER_SHARED_SEAMS = frozenset(
    (
        "src/helpers/ArduinoSerialInterface.cpp",
        "src/helpers/ArduinoSerialInterface.h",
        "src/Dispatcher.cpp",
        "src/Dispatcher.h",
        "examples/simple_repeater/MyMesh.cpp",
        "examples/simple_repeater/MyMesh.h",
        "examples/simple_repeater/main.cpp",
    )
)
_OBSERVER_NATIVE_TEST = "test/test_observer_transport/test_stream_queue.cpp"
_OBSERVER_PLATFORMIO_PATH = "variants/sensecap_solar/platformio.ini"
_SERIES_MANIFEST = "series.json"
_SERIES_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_HISTORICAL_PATCH_DIRECTORY = "observer-patch"
_HISTORICAL_PATCH_TAG = "repeater-v1.17.1"
_HISTORICAL_PATCH_SHA = "d92964352441e53b93e8667b802e04f6e072b39e"


class BuildError(RuntimeError):
    """Raised before a potentially non-reproducible build can occur."""


@dataclass(frozen=True)
class PatchSeries:
    version: str
    upstream_tag: str
    upstream_commit_sha: str
    manifest_sha256: str

    def provenance(self) -> dict[str, str]:
        return {
            "version": self.version,
            "upstream_tag": self.upstream_tag,
            "upstream_commit_sha": self.upstream_commit_sha,
            "manifest_sha256": self.manifest_sha256,
        }


def load_patch_series(patch_dir: Path) -> PatchSeries | None:
    """Load an optional exact-source pin manifest for one versioned series.

    Historical flat patch directories intentionally have no manifest and remain
    available as explicit fixtures. A present manifest is never best-effort.
    """
    manifest_path = patch_dir / _SERIES_MANIFEST
    if manifest_path.is_symlink():
        raise BuildError(f"Patch series manifest must be a regular file: {manifest_path}")
    if not manifest_path.exists():
        if re.fullmatch(r"observer-patch-v\d+\.\d+\.\d+", patch_dir.name):
            raise BuildError(f"Versioned patch directory is missing required {_SERIES_MANIFEST}: {patch_dir}")
        return None
    if not manifest_path.is_file():
        raise BuildError(f"Patch series manifest must be a regular file: {manifest_path}")
    raw = manifest_path.read_bytes()

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BuildError(f"Invalid patch series manifest {manifest_path}: {error}") from error
    required = {"schema_version", "version", "upstream_tag", "upstream_commit_sha"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise BuildError(
            f"Invalid patch series manifest {manifest_path}: expected exactly {sorted(required)}"
        )
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise BuildError(f"Unsupported patch series manifest schema in {manifest_path}")
    version = payload["version"]
    tag = payload["upstream_tag"]
    commit_sha = payload["upstream_commit_sha"]
    if not isinstance(version, str) or not _SERIES_VERSION.fullmatch(version):
        raise BuildError(f"Invalid patch series version in {manifest_path}")
    if patch_dir.name != f"observer-patch-v{version}":
        raise BuildError(f"Patch series directory {patch_dir.name!r} does not match manifest version {version}")
    if not isinstance(tag, str) or tag != f"repeater-v{version}":
        raise BuildError(f"Patch series manifest tag must be repeater-v{version}")
    if not isinstance(commit_sha, str) or not _FULL_COMMIT_SHA.fullmatch(commit_sha):
        raise BuildError(f"Patch series manifest requires a full 40-character commit SHA: {manifest_path}")
    return PatchSeries(
        version=version,
        upstream_tag=tag,
        upstream_commit_sha=commit_sha,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def validate_patch_series_release(patch_dir: Path, release: ReleasePin) -> PatchSeries | None:
    """Fail closed unless a versioned series exactly supports the resolved release."""
    series = load_patch_series(patch_dir)
    if series is None and patch_dir.name == _HISTORICAL_PATCH_DIRECTORY:
        if release.tag != _HISTORICAL_PATCH_TAG or release.commit_sha.lower() != _HISTORICAL_PATCH_SHA:
            raise BuildError(
                f"Historical flat patch series supports only {_HISTORICAL_PATCH_TAG} at "
                f"{_HISTORICAL_PATCH_SHA}; resolved latest is {release.tag} at {release.commit_sha}"
            )
    if series is not None and (
        release.tag != series.upstream_tag or release.commit_sha.lower() != series.upstream_commit_sha
    ):
        raise BuildError(
            f"Patch series {patch_dir.name} supports only {series.upstream_tag} at "
            f"{series.upstream_commit_sha}; resolved latest is {release.tag} at {release.commit_sha}"
        )
    return series


def _validate_patch_series_source(source_dir: Path, series: PatchSeries) -> None:
    if not (source_dir / ".git").exists():
        raise BuildError(f"Patch series {series.upstream_tag} requires a Git checkout at its supported SHA")
    status = _run(["git", "status", "--porcelain"], cwd=source_dir).stdout.strip()
    if status:
        raise BuildError("Source checkout must be clean before applying a versioned observer patch series")
    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=source_dir,
        text=True,
        capture_output=True,
    )
    if branch.returncode == 0:
        raise BuildError("Pinned observer patch series requires a detached immutable source checkout")
    head = _run(["git", "rev-parse", "HEAD"], cwd=source_dir).stdout.strip().lower()
    if head != series.upstream_commit_sha:
        raise BuildError(
            f"Patch series {series.upstream_tag} requires source SHA "
            f"{series.upstream_commit_sha}, got {head}"
        )


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise BuildError(f"Command failed: {' '.join(command)}\n{detail}") from error


def _ensure_clean_detached_source(source_dir: Path, release: ReleasePin) -> None:
    if not (source_dir / ".git").exists():
        raise BuildError("Source must be a clean detached checkout at the resolved immutable commit")
    if _run(["git", "status", "--porcelain"], cwd=source_dir).stdout.strip():
        raise BuildError("Source checkout is dirty; refusing to apply the observer patch")
    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=source_dir,
        text=True,
        capture_output=True,
    )
    if branch.returncode == 0:
        raise BuildError("Source checkout is on a branch; a detached immutable commit is required")
    head = _run(["git", "rev-parse", "HEAD"], cwd=source_dir).stdout.strip().lower()
    if head != release.commit_sha.lower():
        raise BuildError(f"Source checkout commit {head} does not match resolved pin {release.commit_sha}")


def _patch_files(patch_dir: Path) -> list[Path]:
    patches = sorted(patch_dir.glob("*.patch"))
    if not patches:
        raise BuildError(f"No maintained patch files found in {patch_dir}")
    return patches


def _patch_digest(patches: list[Path]) -> str:
    digest = hashlib.sha256()
    for patch in patches:
        digest.update(patch.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(patch.read_bytes())
    return digest.hexdigest()


def _patch_changed_paths(patch: Path) -> list[tuple[str | None, str | None]]:
    paths: list[tuple[str | None, str | None]] = []
    old_path: str | None = None
    for line in patch.read_text(encoding="utf-8").splitlines():
        if line.startswith("--- "):
            old_path = line[4:].split("\t", 1)[0]
        elif line.startswith("+++ "):
            new_path = line[4:].split("\t", 1)[0]
            paths.append((old_path, new_path))
            old_path = None
    if old_path is not None or not paths:
        raise BuildError(f"Patch {patch.name} has malformed or missing file headers")
    return paths


def _normalise_patch_path(path: str | None) -> str | None:
    if path == "/dev/null":
        return None
    if path is None or not (path.startswith("a/") or path.startswith("b/")):
        raise BuildError("Patch uses an unsafe or malformed path")
    normalised = path[2:]
    if not normalised or normalised.startswith("/") or ".." in Path(normalised).parts:
        raise BuildError("Patch uses an unsafe or malformed path")
    return normalised


def validate_observer_patch_scope(patches: list[Path], source_dir: Path | None = None) -> None:
    """Reject every patch path outside the reviewed observer patch contract."""
    if patches:
        patch_dir = patches[0].parent
        if any(patch.parent.resolve() != patch_dir.resolve() for patch in patches):
            raise BuildError("All patches in an ordered series must come from one patch directory")
        series = load_patch_series(patch_dir)
        if series is not None and source_dir is not None:
            _validate_patch_series_source(source_dir, series)
        elif patch_dir.name == _HISTORICAL_PATCH_DIRECTORY and source_dir is not None:
            _validate_patch_series_source(
                source_dir,
                PatchSeries(
                    version="1.17.1",
                    upstream_tag=_HISTORICAL_PATCH_TAG,
                    upstream_commit_sha=_HISTORICAL_PATCH_SHA,
                    manifest_sha256="historical-flat-series",
                ),
            )
    for patch in patches:
        for old_raw, new_raw in _patch_changed_paths(patch):
            old_path, new_path = _normalise_patch_path(old_raw), _normalise_patch_path(new_raw)
            if old_path != new_path and old_path is not None and new_path is not None:
                raise BuildError(f"Patch {patch.name} renames a path; renames are not allowed")
            path = new_path or old_path
            if path is None or not _is_allowed_observer_path(path) and path not in (_OBSERVER_NATIVE_TEST, _OBSERVER_PLATFORMIO_PATH):
                raise BuildError(f"Patch {patch.name} changes {path!r} outside the observer path allowlist")
            if path == _OBSERVER_PLATFORMIO_PATH:
                _validate_custom_environment_addition(patch, source_dir)
            elif old_path is not None:
                _validate_context_bearing_hunks(patch, path)


def _validate_context_bearing_hunks(patch: Path, path: str) -> None:
    """Require existing-source edits to be anchored by at least three context lines."""
    lines = patch.read_text(encoding="utf-8").splitlines()
    in_file = False
    index = 0
    while index < len(lines):
        if lines[index].startswith("diff --git "):
            in_file = False
        elif lines[index].startswith("+++ b/"):
            in_file = lines[index][6:] == path
        elif in_file and lines[index].startswith("@@"):
            header = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[index])
            if header is None:
                raise BuildError(f"Patch {patch.name} has a malformed hunk header for {path!r}")
            context_lines = 0
            index += 1
            while index < len(lines) and not lines[index].startswith(("@@", "diff --git ")):
                if lines[index].startswith(" "):
                    context_lines += 1
                index += 1
            if context_lines < 3:
                raise BuildError(
                    f"Patch {patch.name} changes {path!r} with a context-free or weakly anchored hunk"
                )
            continue
        index += 1


def _validate_custom_environment_addition(patch: Path, source_dir: Path | None) -> None:
    """Allow only an append-only custom environment registration in variant INI."""
    lines = patch.read_text(encoding="utf-8").splitlines()
    headers = [line for line in lines if line.startswith("@@ ")]
    if len(headers) != 1 or not re.match(r"@@ -\d+,?\d* \+\d+,\d+ @@", headers[0]):
        raise BuildError(f"Patch {patch.name} must contain one additive custom environment hunk")
    match = re.match(r"@@ -(\d+),?(\d*) \+(\d+),(\d+) @@", headers[0])
    assert match is not None
    old_start, old_count, _new_start, new_count = match.groups()
    if old_count != "0" or int(new_count) == 0:
        raise BuildError(f"Patch {patch.name} may only append the custom environment")
    if source_dir is not None:
        target = source_dir / _OBSERVER_PLATFORMIO_PATH
        if not target.is_file() or int(old_start) != len(target.read_text(encoding="utf-8").splitlines()):
            raise BuildError(f"Patch {patch.name} must append after existing variant configuration")
    additions = [line[1:] for line in lines[lines.index(headers[0]) + 1:] if line.startswith("+") and not line.startswith("+++")]
    if any(line.startswith("-") or line.startswith(" ") for line in lines[lines.index(headers[0]) + 1:]):
        raise BuildError(f"Patch {patch.name} may not edit existing variant configuration")
    if not additions or additions[0] != "" or additions[1] != f"[env:{CUSTOM_ENVIRONMENT}]":
        raise BuildError(f"Patch {patch.name} must append only [{CUSTOM_ENVIRONMENT}]")
    if any(line.strip().startswith("[") and line != f"[env:{CUSTOM_ENVIRONMENT}]" for line in additions):
        raise BuildError(f"Patch {patch.name} may add exactly one [{CUSTOM_ENVIRONMENT}] section")
    def section_name(line: str) -> str | None:
        normalized = line.strip().split(";", 1)[0].split("#", 1)[0].strip()
        match = re.fullmatch(r"\[([^]\r\n]+)\]", normalized)
        return match.group(1) if match else None

    section_headers = [name for line in additions if (name := section_name(line)) is not None]
    if section_headers != [f"env:{CUSTOM_ENVIRONMENT}"]:
        raise BuildError(f"Patch {patch.name} may add exactly one [{CUSTOM_ENVIRONMENT}] section")
    if any(line.strip().startswith("extra_scripts") for line in additions):
        raise BuildError(f"Patch {patch.name} may not add extra_scripts")


def apply_observer_patch(source_dir: Path, patch_dir: Path) -> None:
    """Apply every maintained patch with zero fuzz; any drift is fatal."""
    patches = _patch_files(patch_dir)
    validate_observer_patch_scope(patches, source_dir)
    for patch in patches:
        try:
            subprocess.run(
                ["git", "apply", "--check", "--verbose", str(patch)],
                cwd=source_dir,
                check=True,
                text=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "apply", "--verbose", str(patch)],
                cwd=source_dir,
                check=True,
                text=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise BuildError(f"Patch drift or invalid patch {patch.name}; refusing fuzzy application: {detail}") from error


def validate_patched_observer_source(
    source_dir: Path,
    patch_dir: Path,
    release: ReleasePin,
) -> None:
    """Require integration invariants on the exact freshly patched artifact source."""
    source_dir = source_dir.resolve()
    series = validate_patch_series_release(patch_dir, release)
    if series is None:
        raise BuildError("Applied-source integration checks require a versioned patch series manifest")
    head = _run(["git", "rev-parse", "HEAD"], cwd=source_dir).stdout.strip().lower()
    if head != release.commit_sha.lower():
        raise BuildError(f"Patched source HEAD {head} differs from resolved release {release.commit_sha}")

    expected_paths = set()
    for patch in _patch_files(patch_dir):
        for old_raw, new_raw in _patch_changed_paths(patch):
            path = _normalise_patch_path(new_raw) or _normalise_patch_path(old_raw)
            if path is not None:
                expected_paths.add(path)
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=source_dir).stdout
    changed_paths = {line[3:] for line in status.splitlines() if len(line) > 3}
    if changed_paths != expected_paths:
        unexpected = sorted(changed_paths - expected_paths)
        missing = sorted(expected_paths - changed_paths)
        raise BuildError(f"Patched source differs from the ordered series; unexpected={unexpected}, missing={missing}")

    main_path = "examples/simple_repeater/main.cpp"
    stock_main = _git_file_at_release(source_dir, release, main_path).decode("utf-8")
    main = _require_source_tokens(
        source_dir / main_path,
        (
            "if (len >= (int)sizeof(command))",
            "command[sizeof(command)-2] = '\\r';",
            "command[sizeof(command)-1] = 0;",
            "observer::transport.service();",
            "board.loop();",
        ),
        "repeater loop bounds/housekeeping",
    )
    for inherited in (
        "if (len >= (int)sizeof(command))",
        "command[sizeof(command)-2] = '\\r';",
        "command[sizeof(command)-1] = 0;",
        "board.loop();",
    ):
        if inherited not in stock_main:
            raise BuildError(f"Resolved stock source no longer provides expected loop invariant: {inherited}")
    loop = re.search(r"(?s)void loop\(\)\s*\{(.*?)\n\}", main)
    if loop is None or "board.loop();" not in loop.group(1):
        raise BuildError("Patched main loop must retain board.loop() housekeeping")

    mesh_path = "examples/simple_repeater/MyMesh.cpp"
    stock_mesh = _git_file_at_release(source_dir, release, mesh_path).decode("utf-8")
    mesh = _require_source_tokens(
        source_dir / mesh_path,
        (
            "_cli.loadPrefs(_fs);",
            "observer::transport.attachPreferences(observerEnabled(), peer, observerHasPeer());",
            "observer::transport.setPreferenceCallbacks(saveObserverEnabled, saveObserverPeer);",
            "board.attachDynamicPrefs(_prefs.getCustom());",
            'if (observerEnabled()) observer::transport.beginFromPreferences("SenseCAP Observer");',
        ),
        "observer boot preference integration",
    )
    begin_start = mesh.find("void MyMesh::begin(FILESYSTEM *fs)")
    begin_end = mesh.find("\nvoid MyMesh::sendFloodScoped", begin_start)
    if begin_start < 0 or begin_end < 0:
        raise BuildError("Could not isolate MyMesh::begin for observer startup validation")
    begin_body = mesh[begin_start:begin_end]
    if "board.attachDynamicPrefs(_prefs.getCustom());" not in stock_mesh:
        raise BuildError("Resolved stock source no longer provides dynamic preference attachment")
    if "_cli.loadPrefs(_fs);" not in stock_mesh:
        raise BuildError("Resolved stock source no longer loads prefs before dynamic preference attachment")
    startup_order = (
        begin_body.find("_cli.loadPrefs(_fs);"),
        begin_body.find("observer::transport.attachPreferences(observerEnabled(), peer, observerHasPeer());"),
        begin_body.find("observer::transport.setPreferenceCallbacks(saveObserverEnabled, saveObserverPeer);"),
        begin_body.find("board.attachDynamicPrefs(_prefs.getCustom());"),
        begin_body.find('if (observerEnabled()) observer::transport.beginFromPreferences("SenseCAP Observer");'),
    )
    if any(position < 0 for position in startup_order) or list(startup_order) != sorted(startup_order):
        raise BuildError("Observer boot startup must follow loaded prefs/callbacks and dynamic prefs")
    if mesh.count("beginFromPreferences(") != 1:
        raise BuildError("Observer boot startup must follow loaded prefs/callbacks and dynamic prefs exactly once")

    _require_source_tokens(
        source_dir / "examples/simple_repeater/MyMesh.h",
        (
            "bool observerEnabled() const { return _prefs.observer_version_ == observer::ObserverNodePrefs::kObserverVersion && _prefs.observer_enabled == 1; }",
        ),
        "schema-validated observer opt-in",
    )

    variant_path = source_dir / _OBSERVER_PLATFORMIO_PATH
    if not variant_path.is_file():
        raise BuildError(f"Patched release is missing SenseCAP PlatformIO config: {variant_path}")
    stock_variant = _git_file_at_release(source_dir, release, _OBSERVER_PLATFORMIO_PATH)
    patched_variant = variant_path.read_bytes()
    if not patched_variant.startswith(stock_variant):
        raise BuildError("Observer environment must append without changing the pinned SenseCAP stock config")
    custom_section = patched_variant[len(stock_variant):].decode("utf-8")
    if custom_section.count(f"[env:{CUSTOM_ENVIRONMENT}]") != 1:
        raise BuildError("Patched SenseCAP config must contain exactly one custom observer environment")
    if "CONFIG_MAX_KEYLEN=19" not in custom_section:
        raise BuildError("Custom observer environment must preserve CONFIG_MAX_KEYLEN=19")
    if "NRF52_POWER_MANAGEMENT" in custom_section or "NRF52_POWER_MANAGEMENT" in stock_variant.decode("utf-8"):
        raise BuildError("Observer series must inherit the pinned release's stock power configuration")


def _git_file_at_release(source_dir: Path, release: ReleasePin, relative_path: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "show", f"{release.commit_sha}:{relative_path}"],
            cwd=source_dir,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError(f"Could not read pinned stock source {relative_path}") from error
    return result.stdout


def _require_source_tokens(path: Path, required: tuple[str, ...], label: str) -> str:
    if not path.is_file():
        raise BuildError(f"Patched release is missing required {label}: {path}")
    text = path.read_text(encoding="utf-8")
    missing = [token for token in required if token not in text]
    if missing:
        raise BuildError(f"Patched release lost required {label} invariant(s): {', '.join(missing)}")
    return text


def _platform_versions(source_dir: Path) -> dict[str, str]:
    result = _run(["pio", "--version"], cwd=source_dir)
    versions = {"platformio": result.stdout.strip()}
    packages = _run(["pio", "pkg", "list", "-e", CUSTOM_ENVIRONMENT], cwd=source_dir)
    versions["packages_report"] = packages.stdout.strip()
    for line in packages.stdout.splitlines():
        match = re.search(r"([^\s]+)\s+@\s+([^\s]+)", line)
        if match:
            versions[match.group(1)] = match.group(2)
    return versions


def _effective_environment(source_dir: Path, environment: str) -> dict[str, object]:
    result = _run(["pio", "project", "config", "--json-output"], cwd=source_dir)
    try:
        configuration = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise BuildError(f"PlatformIO returned invalid effective configuration for {environment}") from error
    try:
        return dict(dict(configuration)[f"env:{environment}"])
    except (TypeError, ValueError, KeyError) as error:
        raise BuildError(f"PlatformIO returned invalid effective configuration for {environment}") from error


def _build_flags(configuration: dict[str, object]) -> list[str]:
    flags = configuration.get("build_flags", [])
    if isinstance(flags, str):
        return flags.split()
    if isinstance(flags, list) and all(isinstance(flag, str) for flag in flags):
        return flags
    raise BuildError("PlatformIO effective build_flags are malformed")


def _logging_enabled(flags: list[str]) -> bool:
    return re.search(r"(?:^|\s)-D\s*MESH_PACKET_LOGGING=1(?:\s|$)", " ".join(flags)) is not None


def _extra_scripts(configuration: dict[str, object]) -> tuple[str, ...]:
    scripts = configuration.get("extra_scripts", [])
    if isinstance(scripts, str):
        return (scripts,)
    if isinstance(scripts, list) and all(isinstance(script, str) for script in scripts):
        return tuple(scripts)
    raise BuildError("PlatformIO effective extra_scripts are malformed")


def _stock_guard_snapshot(source_dir: Path) -> dict[str, bytes]:
    """Capture stock SenseCAP config/source paths the observer patch may not alter."""
    platformio = source_dir / "platformio.ini"
    contents = platformio.read_text(encoding="utf-8")
    section = re.search(rf"(?ms)^\[env:{re.escape(BASELINE_ENVIRONMENT)}\].*?(?=^\[|\Z)", contents)
    if section is None:
        variant_ini = source_dir / _OBSERVER_PLATFORMIO_PATH
        if variant_ini.is_file():
            section = re.search(rf"(?ms)^\[env:{re.escape(BASELINE_ENVIRONMENT)}\].*?(?=^\[|\Z)", variant_ini.read_text(encoding="utf-8"))
    if section is None:
        raise BuildError(f"Resolved source is missing official {BASELINE_ENVIRONMENT} PlatformIO environment")
    snapshot = {"platformio.ini:stock-environment": section.group(0).rstrip().encode("utf-8")}
    variant = source_dir / "variants" / "sensecap_solar"
    if variant.is_dir():
        for path in variant.rglob("*"):
            if not path.is_file():
                continue
            relative = str(path.relative_to(source_dir))
            if relative == _OBSERVER_PLATFORMIO_PATH:
                parts = re.split(r"(?m)(?=^\[)", path.read_text(encoding="utf-8"))
                snapshot[relative] = "".join(part for part in parts if not part.startswith(f"[env:{CUSTOM_ENVIRONMENT}]")).rstrip().encode("utf-8")
            else:
                snapshot[relative] = path.read_bytes()
    return snapshot


def _is_allowed_observer_path(path: str) -> bool:
    return path in _OBSERVER_SHARED_SEAMS or path.startswith(_OBSERVER_PATH_PREFIXES)


def _compile_relevant_snapshot(source_dir: Path) -> dict[str, bytes]:
    """Hash source/config inputs that can affect the stock target, by path."""
    roots = ("src", "include", "lib", "variants", "boards")
    suffixes = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".ini", ".json", ".ld")
    contents = (source_dir / "platformio.ini").read_text(encoding="utf-8")
    sections = re.split(r"(?m)(?=^\[)", contents)
    stock_platformio = "".join(section for section in sections if not section.startswith(f"[env:{CUSTOM_ENVIRONMENT}]")).rstrip()
    snapshot: dict[str, bytes] = {"platformio.ini": stock_platformio.encode("utf-8")}
    for root in roots:
        directory = source_dir / root
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix.lower() in suffixes:
                    relative = str(path.relative_to(source_dir))
                    if not _is_allowed_observer_path(relative) and relative != _OBSERVER_PLATFORMIO_PATH:
                        snapshot[relative] = path.read_bytes()
    return snapshot


def _memory_report(output: str) -> dict[str, int]:
    report: dict[str, int] = {}
    for name, value in re.findall(r"(?im)^\s*(RAM|Flash)\s*:\s*(\d+)", output):
        report[f"{name.lower()}_used"] = int(value)
    for name, used, total in re.findall(r"(?im)^\s*(RAM|Flash)\s*:.*?\(used (\d+) bytes from (\d+) bytes\)", output):
        report[f"{name.lower()}_used"] = int(used)
        report[f"{name.lower()}_total"] = int(total)
    return report


def _copy_artifacts(source_dir: Path, environment: str, output_dir: Path, label: str) -> dict[str, str]:
    artifacts = {}
    for extension in ("hex", "zip", "uf2", "bin"):
        source = source_dir / ".pio" / "build" / environment / f"firmware.{extension}"
        if source.is_file():
            target = output_dir / f"{label}.{extension}"
            shutil.copyfile(source, target)
            artifacts[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    if not artifacts:
        raise BuildError(f"PlatformIO did not produce firmware hex/zip/uf2/bin for {environment}")
    return artifacts


def validate_output_dir(output_dir: Path) -> None:
    """Never overwrite or remove old build results or unknown user files."""
    if output_dir.is_symlink() or (output_dir.exists() and (
        not output_dir.is_dir() or any(output_dir.iterdir())
    )):
        raise BuildError(f"A fresh output directory is required (absent or empty, not a symlink): {output_dir}")


def build_release(
    source_root: Path,
    output_dir: Path,
    compare_stock: bool = False,
    *,
    release: ReleasePin | None = None,
    patch_dir: Path | None = None,
    timestamp: str | None = None,
) -> BuildManifest:
    """Publish complete results atomically to a fresh output directory only.

    Failures may leave the disposable source patched, but never publish partial
    firmware or success provenance. Existing output is never deleted or reused.
    """
    validate_output_dir(output_dir)
    if release is not None:
        selected_patch_dir = patch_dir or source_root / "firmware" / "observer-patch"
        validate_patch_series_release(selected_patch_dir, release)
    output_dir = output_dir.absolute()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}-", dir=output_dir.parent) as temporary:
        staged = Path(temporary)
        manifest = _build_release(source_root, staged, compare_stock,
                                  release=release, patch_dir=patch_dir, timestamp=timestamp)
        validate_output_dir(output_dir)
        staged.replace(output_dir)
    return manifest


def _build_release(
    source_root: Path,
    output_dir: Path,
    compare_stock: bool = False,
    *,
    release: ReleasePin | None = None,
    patch_dir: Path | None = None,
    timestamp: str | None = None,
) -> BuildManifest:
    """Build custom firmware; stock compilation is an explicit opt-in, never flash."""
    source_root = source_root.resolve()
    if release is None:
        raise BuildError("A dynamically resolved immutable release pin is required")
    patch_dir = patch_dir or source_root / "firmware" / "observer-patch"
    series = validate_patch_series_release(patch_dir, release)
    output_dir.mkdir(parents=True, exist_ok=True)
    _ensure_clean_detached_source(source_root, release)
    platformio_ini = source_root / "platformio.ini"
    if not platformio_ini.is_file():
        raise BuildError(f"Resolved source is missing official {BASELINE_ENVIRONMENT} PlatformIO environment")
    patches = _patch_files(patch_dir)
    patch_sha256 = _patch_digest(patches)

    stock_before = _effective_environment(source_root, BASELINE_ENVIRONMENT)
    expected_stock_scripts = _extra_scripts(stock_before)
    stock_files_before = _stock_guard_snapshot(source_root)
    compile_inputs_before = _compile_relevant_snapshot(source_root)
    baseline_command = None
    baseline_memory = None
    artifacts = {}
    if compare_stock:
        baseline_command = ["pio", "run", "-e", BASELINE_ENVIRONMENT]
        baseline_run = _run(baseline_command, cwd=source_root)
        baseline_memory = _memory_report(baseline_run.stdout)
        artifacts.update(_copy_artifacts(source_root, BASELINE_ENVIRONMENT, output_dir, "baseline"))
    apply_observer_patch(source_root, patch_dir)
    stock_after = _effective_environment(source_root, BASELINE_ENVIRONMENT)
    if stock_after != stock_before:
        raise BuildError(f"Observer patch modified stock {BASELINE_ENVIRONMENT} configuration")
    if _extra_scripts(stock_after) != expected_stock_scripts:
        raise BuildError("Observer patch changed stock extra_scripts")
    if _stock_guard_snapshot(source_root) != stock_files_before:
        raise BuildError("Observer patch modified stock SenseCAP configuration/source paths")
    if _compile_relevant_snapshot(source_root) != compile_inputs_before:
        raise BuildError("Observer patch changed compile-relevant stock input outside the observer path allowlist")
    if series is not None:
        validate_patched_observer_source(source_root, patch_dir, release)
    custom = _effective_environment(source_root, CUSTOM_ENVIRONMENT)
    if custom == stock_before:
        raise BuildError("Observer custom environment must be distinct from the stock environment")
    custom_flags = _build_flags(custom)
    if _extra_scripts(custom) != expected_stock_scripts:
        raise BuildError("Custom observer environment may not add extra_scripts beyond upstream stock scripts")
    if not _logging_enabled(custom_flags):
        raise BuildError("Custom effective PlatformIO configuration must enable MESH_PACKET_LOGGING=1")
    custom_command = ["pio", "run", "-e", CUSTOM_ENVIRONMENT]
    custom_run = _run(custom_command, cwd=source_root)
    artifacts.update(_copy_artifacts(source_root, CUSTOM_ENVIRONMENT, output_dir, "observer"))
    manifest = BuildManifest(
        release=release,
        patch_id=patch_dir.name,
        patch_sha256=patch_sha256,
        patch_series=series.provenance() if series is not None else None,
        baseline_environment=BASELINE_ENVIRONMENT,
        custom_environment=CUSTOM_ENVIRONMENT,
        custom_build_flags=custom_flags,
        versions=_platform_versions(source_root),
        commands={"baseline": baseline_command, "custom": custom_command},
        timestamp=timestamp or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        artifacts=artifacts,
        memory={"baseline": baseline_memory, "custom": _memory_report(custom_run.stdout)},
    )
    manifest.write(output_dir / "provenance.json")
    return manifest
