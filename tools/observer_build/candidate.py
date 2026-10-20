"""Temporary, strict retargeting of reviewed firmware patches to stable releases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .build import BuildError, build_release, load_patch_series, validate_output_dir
from .release_resolver import ReleasePin


_STABLE_REPEATER_TAG = re.compile(r"^repeater-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_BUILD_DATE = re.compile(r"^[0-9]{2} [A-Z][a-z]{2} [0-9]{4}$")


class CandidateError(ValueError):
    """Candidate preparation failed closed; the reviewed series is untouched."""


@dataclass(frozen=True)
class CandidateSeries:
    directory: Path
    version: str
    reviewed_manifest_sha256: str
    reviewed_patch_sha256: str
    candidate_manifest_sha256: str
    candidate_patch_sha256: str


def _patch_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    patches = sorted(directory.glob("*.patch"), key=lambda item: item.name)
    if not patches:
        raise CandidateError("Reviewed patch series contains no patch files")
    for path in patches:
        if path.is_symlink() or not path.is_file():
            raise CandidateError("Patch series must contain only regular patch files")
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
    return digest.hexdigest()


def make_candidate_series(reviewed_patch_dir: Path, destination: Path, release: ReleasePin) -> CandidateSeries:
    """Copy identical reviewed patches and retarget only a temporary strict manifest."""
    reviewed_patch_dir = Path(reviewed_patch_dir)
    destination = Path(destination)
    match = _STABLE_REPEATER_TAG.fullmatch(release.tag)
    if match is None or not _FULL_SHA.fullmatch(release.commit_sha.lower()):
        raise CandidateError("Candidate pin must be a numeric stable repeater-vX.Y.Z tag and full immutable SHA")
    version = ".".join(match.groups())
    if destination.name != f"observer-patch-v{version}":
        raise CandidateError("Temporary candidate directory must match the resolved Repeater version")
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise CandidateError("Candidate series destination must be absent or an empty real directory")
    try:
        original = load_patch_series(reviewed_patch_dir)
        if original is None:
            raise CandidateError("A reviewed versioned series manifest is required")
        original_manifest = reviewed_patch_dir / "series.json"
        reviewed_manifest_hash = hashlib.sha256(original_manifest.read_bytes()).hexdigest()
        reviewed_patch_hash = _patch_digest(reviewed_patch_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for patch in sorted(reviewed_patch_dir.glob("*.patch"), key=lambda item: item.name):
            if patch.is_symlink() or not patch.is_file():
                raise CandidateError("Reviewed patch series must contain regular patch files only")
            shutil.copyfile(patch, destination / patch.name)
        payload = {
            "schema_version": 1,
            "version": version,
            "upstream_tag": release.tag,
            "upstream_commit_sha": release.commit_sha.lower(),
        }
        manifest_bytes = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        (destination / "series.json").write_bytes(manifest_bytes)
        candidate = load_patch_series(destination)
        if candidate is None or candidate.upstream_commit_sha != release.commit_sha.lower():
            raise CandidateError("Temporary manifest did not validate against the resolved immutable source")
        candidate_patch_hash = _patch_digest(destination)
        if candidate_patch_hash != reviewed_patch_hash:
            raise CandidateError("Temporary candidate patch bytes differ from the reviewed patch series")
        return CandidateSeries(
            destination, version, reviewed_manifest_hash, reviewed_patch_hash,
            hashlib.sha256(manifest_bytes).hexdigest(), candidate_patch_hash,
        )
    except (BuildError, OSError) as error:
        raise CandidateError(str(error)) from error


def _portable_error(error: Exception, workspace: Path) -> str:
    text = str(error).replace(str(workspace.resolve()), "<workspace>")
    text = re.sub(r"(?<![A-Za-z0-9])/(?:home|tmp|workspace)/[^\s:'\"]+", "<local-path>", text)
    return text[:4000]


def run_candidate_build(
    source_root: Path,
    output_dir: Path,
    reviewed_patch_dir: Path,
    release: ReleasePin,
    *,
    build_date: str,
) -> dict[str, object]:
    """Build exact resolved source with unchanged patches and write a portable result report."""
    source_root = Path(source_root).resolve()
    output_dir = Path(output_dir).absolute()
    reviewed_patch_dir = Path(reviewed_patch_dir).resolve()
    if not _BUILD_DATE.fullmatch(build_date):
        raise CandidateError("Build date must use the firmware convention 'DD Mon YYYY'")
    if output_dir.is_symlink() or (output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir()))):
        raise CandidateError("Candidate output must be absent or an empty real directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "candidate-report.json"
    result: dict[str, object] = {
        "schema_version": 1,
        "candidate_status": "building",
        "release_tag": release.tag,
        "release_commit_sha": release.commit_sha,
        "firmware_version": f"v{release.tag.removeprefix('repeater-v')}-obs544",
        "firmware_build_date": build_date,
        "artifacts": None,
    }
    candidate_root = None
    try:
        with tempfile.TemporaryDirectory(prefix="observer-candidate-series-", dir=output_dir) as temporary:
            candidate_root = Path(temporary) / f"observer-patch-v{release.tag.removeprefix('repeater-v')}"
            series = make_candidate_series(reviewed_patch_dir, candidate_root, release)
            result["reviewed_series"] = {
                "version": load_patch_series(reviewed_patch_dir).version,
                "manifest_sha256": series.reviewed_manifest_sha256,
                "patch_sha256": series.reviewed_patch_sha256,
            }
            result["temporary_candidate_series"] = {
                "manifest_sha256": series.candidate_manifest_sha256,
                "patch_sha256": series.candidate_patch_sha256,
            }
            flags = (
                f"-DFIRMWARE_VERSION='\"{result['firmware_version']}\"' "
                f"-DFIRMWARE_BUILD_DATE='\"{build_date}\"'"
            )
            previous_flags = os.environ.get("PLATFORMIO_BUILD_FLAGS")
            os.environ["PLATFORMIO_BUILD_FLAGS"] = flags
            try:
                artifact_dir = output_dir / "artifacts"
                manifest = build_release(
                    source_root,
                    artifact_dir,
                    release=release,
                    patch_dir=series.directory,
                )
                from .native_compatibility import run_native_compatibility_tests
                run_native_compatibility_tests(source_root)
            finally:
                if previous_flags is None:
                    os.environ.pop("PLATFORMIO_BUILD_FLAGS", None)
                else:
                    os.environ["PLATFORMIO_BUILD_FLAGS"] = previous_flags
            result["candidate_status"] = "unapproved-candidate"
            result["artifacts"] = manifest.artifacts
            result["exact_source_native_tests"] = "passed"
            provenance_bytes = (artifact_dir / "provenance.json").read_bytes()
            result["build_provenance_sha256"] = hashlib.sha256(provenance_bytes).hexdigest()
            result["limitations"] = [
                "Temporary manifest retargets identical reviewed patches; it is not a reviewed upstream port.",
                "Candidate requires human source/behavior review and all separate hardware gates before use.",
                "No flash, publication, or stable release was performed.",
            ]
    except Exception as error:
        message = _portable_error(error, source_root.parent)
        result["candidate_status"] = "incompatible-or-build-failed"
        result["error"] = message
        result["limitations"] = [
            "No older upstream pin was substituted.",
            "No candidate firmware artifact should be used.",
        ]
        report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise CandidateError(message) from error
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
