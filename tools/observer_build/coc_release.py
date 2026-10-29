"""Replay immutable CoC inputs without another Git worktree or network access."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile


RECIPE = "firmware/observer-coc-release.json"


def build_epoch(build_date: str) -> int:
    return int(datetime.strptime(build_date, "%d %b %Y").replace(tzinfo=timezone.utc).timestamp())


def memory_report(output: str) -> dict[str, int]:
    report = {}
    for name, used, total in re.findall(r"(?im)^\s*(RAM|Flash)\s*:.*?\(used (\d+) bytes from (\d+) bytes\)", output):
        report[name.lower() + "_used"] = int(used)
        report[name.lower() + "_total"] = int(total)
    return report


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def cache_fingerprint(root: Path) -> str:
    """Bind complete tool/framework contents and executable modes, not metadata alone."""
    if not root.is_dir():
        raise ValueError("Missing cache input directory")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            if path.is_dir() and root.resolve() not in path.resolve().parents:
                raise ValueError("External directory symlink in cache input")
            digest.update(str(relative).encode() + b"\0symlink\0" + os.readlink(path).encode() + b"\0")
        if path.is_file():
            digest.update(str(relative).encode() + b"\0" + str(path.stat().st_mode & 0o777).encode()
                          + b"\0" + sha256(path).encode() + b"\0")
    return digest.hexdigest()


def verify_cached_inputs(recipe: dict, packages: Path, platforms: Path, pio_venv: Path) -> dict:
    expected = recipe.get("build_inputs")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Recipe must pin complete cache input fingerprints")
    observed = {}
    for name, fingerprint in expected.items():
        if name == "pio-venv":
            path = pio_venv
        else:
            parts = Path(name).parts
            if len(parts) != 2 or parts[0] not in {"packages", "platforms"} or parts[1] == "..":
                raise ValueError("Invalid cache input path")
            path = (packages if parts[0] == "packages" else platforms) / parts[1]
        actual = cache_fingerprint(path)
        if actual != fingerprint:
            raise ValueError("Changed cache input: " + name)
        observed[name] = actual
    return observed


def input_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe recipe path")
    result = root / path
    if not result.is_file() or result.is_symlink() or root.resolve() not in result.resolve().parents:
        raise ValueError("Recipe path must be a regular file within maintained root")
    return result


def load_recipe(root: Path, recipe_path: str = RECIPE) -> dict:
    recipe = json.loads(input_path(root, recipe_path).read_text())
    if (recipe.get("schema_version") != 1
            or not re.fullmatch(r"v\d+\.\d+\.\d+-coc", recipe.get("version", ""))
            or not re.fullmatch(r"[0-9a-f]{40}", recipe.get("upstream_commit", ""))
            or recipe.get("environment") != "SenseCap_Solar_repeater_observer"
            or not recipe.get("patches")):
        raise ValueError("Invalid pinned CoC recipe")
    seen = set()
    for entry in recipe["patches"]:
        path = input_path(root, entry["path"])
        if entry["path"] in seen or sha256(path) != entry["sha256"]:
            raise ValueError("Recipe patch hash mismatch or duplicate path")
        seen.add(entry["path"])
    if framework := recipe.get("framework"):
        if sha256(input_path(root, framework["patch"])) != framework["patch_sha256"]:
            raise ValueError("Framework patch hash mismatch")
    return recipe


def source_hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): sha256(path) for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
            and not any(part in {".git", ".pio", "__pycache__", "target"} for part in path.relative_to(root).parts)}


def validate_application_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as package:
        manifest = json.loads(package.read("manifest.json"))["manifest"]
        if (package.testzip() is not None or "application" not in manifest
                or set(manifest) - {"application", "dfu_version"}):
            raise ValueError("DFU output is not an intact application-only ZIP")
        application = manifest["application"]
        if set(package.namelist()) != {"manifest.json", application["bin_file"], application["dat_file"]}:
            raise ValueError("DFU ZIP includes content outside its application-only manifest")
        return manifest


def normalize_application_zip(path: Path, epoch: int) -> None:
    """Only ZIP container metadata changes; DFU application/init bytes stay exact."""
    validate_application_zip(path)
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    stamp = datetime.fromtimestamp(epoch, timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(files.items()):
            info = zipfile.ZipInfo(name, stamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    validate_application_zip(path)


def prepare_sources(root: Path, upstream: Path, output: Path, *, recipe_path: str = RECIPE) -> dict:
    """Archive the pinned Git object, not working files; never overwrite outputs."""
    recipe = load_recipe(root, recipe_path)
    if output.exists() or output.is_symlink():
        raise ValueError("A fresh absent output directory is required")
    archive = subprocess.run(
        ["git", "-C", str(upstream), "archive", "--format=tar", recipe["upstream_commit"]],
        check=True, capture_output=True,
    ).stdout
    output.mkdir(parents=True)
    source = output / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        tree.extractall(source, filter="data")
    apply_env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(output.resolve()))
    for entry in recipe["patches"]:
        patch = input_path(root, entry["path"])
        subprocess.run(["git", "apply", "--check", str(patch.resolve())], cwd=source, env=apply_env, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(patch.resolve())], cwd=source, env=apply_env, check=True, capture_output=True)
        saved = output / "inputs" / entry["path"]
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(patch.read_bytes())
    manifest = {"recipe": recipe, "source_sha256": source_hashes(source), "status": "prepared-not-built"}
    (output / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
