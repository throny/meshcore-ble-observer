#!/usr/bin/env python3
"""Build-only, offline CoC release candidate. Never invokes upload or hardware."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess

from observer_build.coc_release import input_path, prepare_sources, sha256, source_hashes, validate_application_zip, verify_cached_inputs, build_epoch, normalize_application_zip, memory_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True, help="cached Git repository containing the pinned upstream object")
    parser.add_argument("--output", type=Path, required=True, help="fresh ignored build-output directory")
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--platforms", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, required=True, help="cached matching PlatformIO environment libdeps")
    parser.add_argument("--pio-venv", type=Path, required=True)
    parser.add_argument("--host", type=Path, required=True, help="matching host/helper sources, never config or credentials")
    parser.add_argument("--build-date", required=True, help="DD Mon YYYY")
    args = parser.parse_args()
    datetime.strptime(args.build_date, "%d %b %Y")
    root = Path(__file__).resolve().parents[1]
    output = args.output.absolute()
    manifest = prepare_sources(root, args.upstream, output)
    recipe = manifest["recipe"]
    manifest["verified_build_inputs"] = verify_cached_inputs(recipe, args.packages, args.platforms, args.pio_venv)
    manifest["compiler_version"] = subprocess.run(
        [str(args.packages / "toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++"), "--version"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    manifest["platform_version"] = json.loads((args.platforms / "nordicnrf52/platform.json").read_text())["version"]
    framework = args.packages / "framework-arduinoadafruitnrf52"
    seam = recipe["framework"]
    original = input_path(framework, seam["path"])
    if sha256(original) != seam["original_sha256"]:
        raise ValueError("Cached framework is not the pinned original")
    overlay = output / "framework-overlay"
    patched = overlay / seam["path"]
    patched.parent.mkdir(parents=True)
    shutil.copyfile(original, patched)
    subprocess.run(["git", "apply", str(input_path(root, seam["patch"]).resolve())], cwd=overlay,
                   env=dict(os.environ, GIT_CEILING_DIRECTORIES=str(output)), check=True)
    saved_patch = output / "inputs" / seam["patch"]
    saved_patch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / seam["patch"], saved_patch)
    source = output / "source"
    # The overlay must carry the exact maintained implementation, not a stale header.
    for name in ("ObserverBleRuntime.cpp", "ObserverBleRuntime.h", "coc/Server.h", "coc/NordicPort.h",
                 "coc/Protocol.h", "coc/Delivery.h", "coc/S140Config.cpp"):
        if (source / "src/observer" / name).read_bytes() != (root / "src/observer" / name).read_bytes():
            raise ValueError("Runtime patch and maintained source differ: " + name)
    deps = source / ".pio/libdeps" / recipe["environment"]
    # PlatformIO uses cached VCS metadata to match pinned Git dependencies.
    # Keep it in this disposable copy, never mutate the shared cache.
    shutil.copytree(args.dependencies, deps, ignore=shutil.ignore_patterns("__pycache__"))
    manifest["dependency_source_sha256"] = source_hashes(deps)
    app = output / "host"
    app.mkdir()
    for name in ("mctomqtt.py", "auth_token.py", "config_loader.py", "pyproject.toml", "uv.lock", "LICENSE"):
        file = args.host / name
        if file.is_file():
            shutil.copyfile(file, app / name)
    shutil.copytree(args.host / "bridge", app / "bridge", ignore=shutil.ignore_patterns("__pycache__"))
    helper = app / "coc-helper"
    helper.mkdir()
    for name in ("Cargo.toml", "Cargo.lock"):
        shutil.copyfile(args.host / "coc-helper" / name, helper / name)
    shutil.copytree(args.host / "coc-helper/src", helper / "src")
    manifest["host_source_sha256"] = source_hashes(app)
    manifest["framework_original_sha256"] = sha256(original)
    manifest["framework_overlay_sha256"] = sha256(patched)
    flags = (f'-DSENSECAP_OBSERVER_COC_CHANNELS=2 -DFIRMWARE_VERSION=\'"{recipe["version"]}"\' '
             f'-DFIRMWARE_BUILD_DATE=\'"{args.build_date}"\'')
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
    for path in ("/usr", "/lib", "/lib64"):
        command += ["--ro-bind", path, path]
    for host, target in ((args.packages, "/packages"), (args.platforms, "/platforms"), (args.pio_venv, "/tools")):
        command += ["--ro-bind", str(host.resolve()), target]
    command += ["--ro-bind", str(patched), "/packages/framework-arduinoadafruitnrf52/" + seam["path"],
                "--bind", str(output), "/release", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--chdir", "/release/source", "--clearenv"]
    for key, value in {"PATH": "/tools/bin:/usr/bin", "HOME": "/release/home", "LANG": "C",
                       "TZ": "UTC", "SOURCE_DATE_EPOCH": str(build_epoch(args.build_date)), "PYTHONHASHSEED": "0",
                       "PLATFORMIO_CORE_DIR": "/release/pio-core", "PLATFORMIO_PACKAGES_DIR": "/packages",
                       "PLATFORMIO_PLATFORMS_DIR": "/platforms", "PLATFORMIO_BUILD_FLAGS": flags,
                       "PLATFORMIO_SETTING_ENABLE_TELEMETRY": "no",
                       "PLATFORMIO_SETTING_CHECK_PLATFORMIO_INTERVAL": "0",
                       "PLATFORMIO_SETTING_CHECK_PLATFORMS_INTERVAL": "0",
                       "PLATFORMIO_SETTING_CHECK_LIBRARIES_INTERVAL": "0"}.items():
        command += ["--setenv", key, value]
    pio = command + ["/tools/bin/python", "-m", "platformio"]
    manifest["build_flags"] = flags
    manifest["build_date"] = args.build_date
    manifest["source_date_epoch"] = build_epoch(args.build_date)
    manifest["network_disabled"] = True
    manifest["production_changes"] = False
    manifest["platformio_version"] = subprocess.run(pio + ["--version"], check=True, capture_output=True, text=True).stdout.strip()
    config = subprocess.run(pio + ["project", "config", "--json-output"], check=True, capture_output=True, text=True)
    (output / "effective-config.json").write_text(config.stdout)
    (output / "build-context.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with (output / "build.log").open("w") as log:
        subprocess.run(pio + ["run", "-e", recipe["environment"], "-j", "2"], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        subprocess.run(pio + ["run", "-e", recipe["environment"], "-t", "create_uf2", "-j", "2"], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    built = source / ".pio/build" / recipe["environment"]
    elf = (built / "firmware.elf").read_bytes()
    if recipe["version"].encode() + b"\0" not in elf or args.build_date.encode() + b"\0" not in elf:
        raise ValueError("ELF does not contain the exact custom version and build date")
    artifacts = output / "artifacts"
    artifacts.mkdir()
    for ext in ("elf", "hex", "zip", "uf2"):
        shutil.copyfile(built / ("firmware." + ext), artifacts / (recipe["version"] + "." + ext))
    normalize_application_zip(artifacts / (recipe["version"] + ".zip"), manifest["source_date_epoch"])
    manifest["dfu_manifest"] = validate_application_zip(artifacts / (recipe["version"] + ".zip"))
    uf2 = (artifacts / (recipe["version"] + ".uf2")).read_bytes()
    import struct
    for offset in range(0, len(uf2), 512):
        magic1, magic2, flags_, address, length, block, count, family = struct.unpack_from("<8I", uf2, offset)
        if (magic1 != 0x0A324655 or magic2 != 0x9E5D5157 or family != 0xADA52840
                or length != 256 or not flags_ & 0x2000 or block != offset // 512 or count != len(uf2) // 512
                or not 0x27000 <= address < address + length <= 0xED000
                or struct.unpack_from("<I", uf2, offset + 508)[0] != 0x0AB16F30):
            raise ValueError("UF2 is outside the application-only bounds")
    manifest["memory"] = memory_report((output / "build.log").read_text())
    manifest["artifacts"] = source_hashes(artifacts)
    manifest["status"] = "unflashed-local-candidate"
    (output / "provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"version": recipe["version"], "memory": manifest["memory"], "artifacts": manifest["artifacts"]}, indent=2))


if __name__ == "__main__":
    main()
