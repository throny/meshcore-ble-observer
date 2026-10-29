"""Pinned source replay and release identity fail closed, without build/network I/O."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from observer_build import coc_release


def fixture(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "version.txt").write_text("stock\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(upstream), *args], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    root = tmp_path / "maintained"
    root.mkdir()
    patch = root / "runtime.patch"
    patch.write_text("--- a/version.txt\n+++ b/version.txt\n@@ -1 +1 @@\n-stock\n+custom\n")
    recipe = {"schema_version": 1, "version": "v1.18.0-coc", "environment": "SenseCap_Solar_repeater_observer",
              "upstream_commit": commit, "patches": [{"path": patch.name, "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}]}
    (root / "recipe.json").write_text(json.dumps(recipe))
    return root, upstream


def test_replay_uses_immutable_git_blob_not_dirty_upstream_working_files(tmp_path):
    root, upstream = fixture(tmp_path)
    (upstream / "version.txt").write_text("unrelated user work\n")
    output = tmp_path / "release"
    coc_release.prepare_sources(root, upstream, output, recipe_path="recipe.json")
    assert (output / "source/version.txt").read_text() == "custom\n"
    assert (upstream / "version.txt").read_text() == "unrelated user work\n"
    assert not (output / "source/.git").exists()
    assert json.loads((output / "inputs.json").read_text())["recipe"]["version"] == "v1.18.0-coc"


def test_changed_patch_is_rejected_before_output_created(tmp_path):
    root, upstream = fixture(tmp_path)
    (root / "runtime.patch").write_text("tampered")
    output = tmp_path / "release"
    with pytest.raises(ValueError, match="hash"):
        coc_release.prepare_sources(root, upstream, output, recipe_path="recipe.json")
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    root, upstream = fixture(tmp_path)
    output = tmp_path / "release"
    output.mkdir()
    (output / "keep.txt").write_text("user work")
    with pytest.raises(ValueError, match="fresh"):
        coc_release.prepare_sources(root, upstream, output, recipe_path="recipe.json")
    assert (output / "keep.txt").read_text() == "user work"


def test_recipe_cannot_escape_maintained_root(tmp_path):
    root, upstream = fixture(tmp_path)
    path = root / "recipe.json"
    recipe = json.loads(path.read_text())
    recipe["patches"][0]["path"] = "../runtime.patch"
    path.write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match="path"):
        coc_release.prepare_sources(root, upstream, tmp_path / "release", recipe_path="recipe.json")


def test_firmware_recipe_pins_version_and_all_patch_bytes():
    root = Path(__file__).resolve().parents[2]
    recipe = coc_release.load_recipe(root)
    assert recipe["version"] == "v1.18.0-coc"
    assert recipe["upstream_commit"] == "81dbae592ffad40b4212bc0b37e82b38da08f4bc"
    assert len(recipe["patches"]) == 9


def test_application_zip_accepts_dfu_format_metadata_but_not_bootloader(tmp_path):
    package = tmp_path / "application.zip"
    def write(manifest):
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"manifest": manifest}))
            archive.writestr("firmware.bin", b"application")
            archive.writestr("firmware.dat", b"init")
    application = {"bin_file": "firmware.bin", "dat_file": "firmware.dat"}
    write({"application": application, "dfu_version": 0.5})
    assert coc_release.validate_application_zip(package)["application"] == application
    write({"application": application, "bootloader": {}})
    with pytest.raises(ValueError, match="application-only"):
        coc_release.validate_application_zip(package)


def test_cached_framework_and_compiler_changes_are_rejected(tmp_path):
    packages = tmp_path / "packages"
    framework = packages / "framework"
    compiler = packages / "compiler"
    framework.mkdir(parents=True)
    compiler.mkdir()
    source = framework / "other-source.cpp"
    source.write_text("reviewed framework")
    binary = compiler / "gcc"
    binary.write_bytes(b"reviewed compiler")
    recipe = {"build_inputs": {
        "packages/framework": coc_release.cache_fingerprint(framework),
        "packages/compiler": coc_release.cache_fingerprint(compiler),
    }}
    assert coc_release.verify_cached_inputs(recipe, packages, tmp_path, tmp_path) == recipe["build_inputs"]
    source.write_text("modified framework, same metadata")
    with pytest.raises(ValueError, match="cache input"):
        coc_release.verify_cached_inputs(recipe, packages, tmp_path, tmp_path)
    source.write_text("reviewed framework")
    binary.write_bytes(b"modified compiler, same metadata")
    with pytest.raises(ValueError, match="cache input"):
        coc_release.verify_cached_inputs(recipe, packages, tmp_path, tmp_path)


def test_build_epoch_and_zip_metadata_do_not_depend_on_wall_clock(tmp_path):
    assert coc_release.build_epoch("29 Oct 2026") == 1793232000
    packages = [tmp_path / "one.zip", tmp_path / "two.zip"]
    application = {"bin_file": "firmware.bin", "dat_file": "firmware.dat"}
    payloads = {"manifest.json": json.dumps({"manifest": {"application": application, "dfu_version": 0.5}}).encode(),
                "firmware.bin": b"binary", "firmware.dat": b"init"}
    for path, year in zip(packages, (2001, 2026)):
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in payloads.items():
                archive.writestr(zipfile.ZipInfo(name, (year, 1, 1, 0, 0, 0)), data)
        coc_release.normalize_application_zip(path, 1793232000)
    assert packages[0].read_bytes() == packages[1].read_bytes()


def test_memory_report_reads_platformio_summary():
    text = "RAM: [= ] 16.3% (used 38460 bytes from 235520 bytes)\nFlash: [== ] 47.6% (used 386264 bytes from 811008 bytes)"
    assert coc_release.memory_report(text) == {
        "ram_used": 38460, "ram_total": 235520,
        "flash_used": 386264, "flash_total": 811008,
    }
