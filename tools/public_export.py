#!/usr/bin/env python3
"""Create a clean, history-free public source snapshot from committed allowlisted files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from observer_build.bridge_candidate import BridgeCandidateError, _included, _privacy_findings


class PublicExportError(ValueError):
    """Safe export error; do not include local paths, values, or file content."""


_EXACT = frozenset({
    "license.txt",
    ".github/workflows/observer-ble.yml",
    "firmware/observer-baseline.json",
    "tools/observer_build.py",
    "tools/observer_build_candidate.py",
    "tools/observer_bridge_candidate.py",
    "tools/public_export.py",
    "tools/hardware/p1pro_usb_common.py",
    "tools/hardware/p1pro_public_backup.py",
    "tests/observer_build/test_bridge_candidate.py",
    "tests/observer_build/test_candidate.py",
    "tests/observer_build/test_ci.py",
    "tests/observer_build/test_lines544_series.py",
    "tests/observer_build/test_native_compatibility.py",
    "tests/observer_build/test_provenance.py",
    "tests/observer_build/test_public_docs.py",
    "tests/observer_build/test_public_export.py",
    "tests/observer_build/test_release_resolver.py",
    "tests/observer_build/test_selected_baseline.py",
    "tests/observer_build/test_source_validation.py",
    "tests/hardware/test_p1pro_public_backup.py",
    "tests/hardware/test_p1pro_usb_common.py",
    "docs/observer/quickstart.md",
    "docs/observer/bridge-setup.md",
    "docs/observer/upstream-contributions.md",
    "docs/observer/public-export-readme.md",
})
_PREFIXES = (
    "tools/observer_build/",
    "observer/meshcoretomqtt/",
    "tests/observer_build/fixtures/",
    "firmware/observer-lines544-release/observer-patch-v1.18.0/",
)
_PATCH_DIR = "firmware/observer-lines544-release/observer-patch-v1.18.0/"
_PUBLIC_GITIGNORE = """out/
.venv/
__pycache__/
.pytest_cache/
.superpowers/
private-backups/
*.gpg
*.pcap
*.log
"""
_DOC_COPY_MAP = {
    "docs/observer/public-export-readme.md": "README.md",
    "docs/observer/upstream-contributions.md": "CONTRIBUTING.md",
}


def _git(root: Path, *args: str):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise PublicExportError("Git source snapshot operation failed; local details suppressed") from None


def _is_allowed(path: str) -> bool:
    if path in _EXACT:
        return True
    if path.startswith(_PREFIXES):
        if path.startswith(_PATCH_DIR):
            return path.endswith(".patch") or path == _PATCH_DIR + "series.json"
        if path.startswith("observer/meshcoretomqtt/"):
            return _included(path.removeprefix("observer/meshcoretomqtt/"))
        if path.startswith("tests/observer_build/"):
            return path.endswith(".py")
        if path.startswith("tools/observer_build/"):
            return path.endswith(".py")
        return True
    return False


def _tracked_tree(repo: Path) -> dict[str, tuple[str, bytes]]:
    listing = _git(repo, "ls-tree", "-rz", "--full-tree", "HEAD").stdout
    selected = {}
    for record in listing.split("\0"):
        if not record:
            continue
        header, separator, name = record.partition("\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise PublicExportError("Committed source tree has an unsupported entry")
        mode, kind, object_id = fields
        if not _is_allowed(name):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise PublicExportError("Public allowlist contains a non-regular source entry")
        try:
            content = subprocess.run(
                ["git", "-C", str(repo), "show", f"HEAD:{name}"],
                check=True, capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            raise PublicExportError("Committed allowlisted source could not be read") from None
        selected[name] = (mode, content)
    missing = _EXACT - set(selected)
    if missing:
        raise PublicExportError("Required public source files are absent from the committed tree")
    return selected


def _validate_destination(repo: Path, output: Path) -> Path:
    output = Path(output).absolute()
    for component in (output, *output.parents):
        if component.is_symlink():
            raise PublicExportError("Export destination must not contain symlink components")
    repo = repo.resolve()
    resolved = output.resolve()
    if resolved == repo or repo in resolved.parents:
        raise PublicExportError("Public export destination must be outside the source worktree")
    if output.exists():
        try:
            metadata = output.stat()
        except OSError:
            raise PublicExportError("Export destination is unavailable") from None
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700 or any(output.iterdir())):
            raise PublicExportError("Export destination must be a fresh caller-owned mode-0700 directory")
    return output


def _write_file(root: Path, relative: str, content: bytes, mode: str = "100644") -> None:
    target = root / PurePosixPath(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o755 if mode == "100755" else 0o644)


def _scan_public_files(root: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise PublicExportError("Public source allowlist contains a non-text file") from None
        try:
            findings = _privacy_findings(text)
        except BridgeCandidateError:
            raise PublicExportError("Public source privacy scan failed safely") from None
        if findings:
            raise PublicExportError("Public source privacy scan rejected candidate content")


def create_public_export(source_root: Path, output_dir: Path) -> dict[str, object]:
    """Materialize a clean, new Git root from committed allowlisted sources only."""
    source_root = Path(source_root).resolve()
    output_dir = _validate_destination(source_root, output_dir)
    try:
        top = Path(_git(source_root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    except PublicExportError:
        raise
    if top != source_root:
        raise PublicExportError("Source root must be the top-level Git worktree")
    tree = _tracked_tree(source_root)
    status_paths = [*sorted(_EXACT), *[prefix.rstrip("/") for prefix in _PREFIXES]]
    status = _git(source_root, "status", "--porcelain", "--", *status_paths).stdout
    if status:
        raise PublicExportError("Allowlisted source has uncommitted changes; export a committed snapshot")

    try:
        output_dir.mkdir(parents=True, mode=0o700)
    except OSError:
        raise PublicExportError("Export destination could not be created") from None
    created_output = True
    try:
        file_contents = {name: data for name, (_, data) in tree.items()}
        # Root-level public entry points are generic copies; source docs stay linked.
        for source, destination in _DOC_COPY_MAP.items():
            file_contents[destination] = tree[source][1]
        file_contents[".gitignore"] = _PUBLIC_GITIGNORE.encode("utf-8")
        _scan_public_files(output_dir, file_contents)
        for name, content in file_contents.items():
            mode = tree[name][0] if name in tree else "100644"
            _write_file(output_dir, name, content, mode)
        files = {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(file_contents.items())
        }
        manifest = {
            "schema_version": 1,
            "kind": "clean-public-export",
            "files": files,
            "manifest_self_hash_excluded": True,
            "limitations": [
                "This is a source snapshot with a new single-commit local history.",
                "It contains no local worktree history, ignored files, hardware records, or private recovery tooling.",
                "The manifest hashes exported paths and does not identify the source workspace commit.",
            ],
        }
        (output_dir / "PUBLIC-EXPORT-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(output_dir), "init", "--quiet", "--initial-branch=public-export"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(output_dir), "add", "-A"], check=True, capture_output=True)
        subprocess.run([
            "git", "-C", str(output_dir),
            "-c", "user.name=Public Export Snapshot",
            "-c", "user.email=public-export@example.invalid",
            "commit", "--quiet", "-m", "chore: create clean public source snapshot",
        ], check=True, capture_output=True)
        return manifest
    except PublicExportError:
        if created_output:
            shutil.rmtree(output_dir)
        raise
    except (OSError, subprocess.CalledProcessError, ValueError):
        if created_output:
            shutil.rmtree(output_dir)
        raise PublicExportError("Public export generation failed; local paths and details suppressed") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="fresh caller-owned mode-0700 directory outside the checkout")
    args = parser.parse_args()
    try:
        manifest = create_public_export(Path(__file__).resolve().parents[1], args.output)
        print(f"Clean public export created: {args.output}")
        print(f"Manifest covers {len(manifest['files'])} allowlisted files; local history was not copied.")
        return 0
    except PublicExportError as error:
        print(f"Public export rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
