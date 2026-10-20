#!/usr/bin/env python3
"""Capture a read-only, public-identity/settings snapshot from a P1-Pro USB CLI.

This is not a private-key backup, full-device image, or validated restore.
"""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

try:
    from p1pro_usb_common import (
        BackupError, SerialCLI, SUPPORTED_REPEATER_VERSION,
        encrypt_verified, value, verify_usb,
    )
except ModuleNotFoundError:
    from tools.hardware.p1pro_usb_common import (
        BackupError, SerialCLI, SUPPORTED_REPEATER_VERSION,
        encrypt_verified, value, verify_usb,
    )


PUBLIC_GETTERS = (
    "get radio", "get tx", "get repeat", "get af", "get int.thresh",
    "get cad", "get agc.reset.interval", "get multi.acks",
    "get allow.read.only", "get flood.advert.interval", "get advert.interval",
    "get radio.rxgain", "get radio.fem.rxgain", "get radio.fem.txgain",
    "get rxdelay", "get txdelay", "get flood.max", "get flood.max.unscoped",
    "get flood.max.advert", "get direct.txdelay", "get path.hash.mode",
    "get loop.detect", "get adc.multiplier", "powersaving", "region",
    "region home", "region default",
)


def capture(cli, expected_public_key=None):
    commands = ("ver", "board", "get name", "get public.key", *PUBLIC_GETTERS)
    replies = {}
    for command in commands:
        reply = cli.query(command)
        replies[command] = reply
        if command == "get public.key":
            public_key = value(reply).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", public_key):
                raise BackupError("Device public identity has an invalid format.")
            if cli.public_key != public_key:
                raise BackupError("Device public identity changed during capture.")
            if expected_public_key is not None and public_key != expected_public_key.lower():
                raise BackupError("Connected device does not match the supplied full public identity.")
        elif command == "board" and value(reply) != "Seeed SenseCap Solar":
            raise BackupError("Connected board is not a supported SenseCAP Solar repeater.")
        elif command == "ver" and not SUPPORTED_REPEATER_VERSION.fullmatch(value(reply)):
            raise BackupError("Repeater firmware version format is unsupported.")
    if cli.public_key != public_key:
        raise BackupError("Device public identity changed during capture.")
    return {
        "schema_version": 1,
        "kind": "public-read-only-cli-snapshot",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "identity": {"public_key": public_key},
        "read_only_replies": replies,
        "complete_backup": False,
        "identity_recovery_included": False,
        "restore_validated": False,
        "coverage_gaps": [
            "No private key or ACL recovery material is included.",
            "This is not a filesystem image and does not capture every device setting.",
            "CLI getter availability/precision may vary by firmware.",
            "No restore has been performed or validated.",
        ],
    }


def encrypted_recovery_payload(cli, public_snapshot):
    private_key = value(cli.query("get prv.key")).lower()
    if not re.fullmatch(r"[0-9a-f]{128}", private_key) or int(private_key, 16) == 0:
        raise BackupError("Private identity export was not a valid-length key.")
    payload = json.loads(json.dumps(public_snapshot))
    payload["kind"] = "encrypted-private-identity-cli-snapshot"
    payload["identity"]["private_key"] = private_key
    payload["identity_recovery_included"] = True
    payload["coverage_gaps"].extend([
        "Private/public key cryptographic consistency was not independently validated.",
        "The archive is not a filesystem image and ACL recovery material is not included.",
        "No restore has been performed or proven; manual restore review is required.",
    ])
    return payload


def validate_directory(path, repo_root=None):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise BackupError("Output directory must not contain symlink components.")
    root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    resolved = path.resolve()
    if resolved == root or root in resolved.parents:
        raise BackupError("Backup output must be outside the repository.")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise BackupError("Output directory must be caller-owned with mode 0700.")


def write_snapshot(directory, snapshot):
    validate_directory(directory)
    target = Path(directory) / ("p1pro-public-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    data = (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="explicit USB serial device, e.g. /dev/ttyACM0")
    parser.add_argument("--destination", type=Path, required=True, help="existing caller-owned mode-0700 directory outside this repository")
    parser.add_argument("--expected-public-key", help="optional 64-hex-character identity pin; device names are not identity")
    parser.add_argument("--include-private", action="store_true", help="also export identity recovery material to a separate GPG-encrypted file")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if not re.fullmatch(r"/dev/ttyACM[0-9]+", args.port):
            raise BackupError("Specify an explicit /dev/ttyACM device path.")
        if args.expected_public_key is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_public_key):
            raise BackupError("Expected identity must be a full 64-character public key.")
        validate_directory(args.destination)
        password = None
        if args.include_private:
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise BackupError("Encrypted recovery requires an interactive terminal for password entry.")
            if not shutil.which("gpg"):
                raise BackupError("GPG is unavailable; no private identity export requested.")
            password = getpass.getpass("New encrypted recovery password (12+ characters): ")
            confirmation = getpass.getpass("Repeat recovery password: ")
            if len(password) < 12 or password != confirmation or "\n" in password or "\r" in password:
                raise BackupError("Password confirmation/length failed; no private identity export requested.")
        verify_usb(args.port)
        allowed = {"ver", "board", "get name", "get public.key", *PUBLIC_GETTERS}
        if args.include_private:
            allowed.add("get prv.key")
        with SerialCLI(args.port, allowed_commands=allowed) as cli:
            snapshot = capture(cli, expected_public_key=args.expected_public_key)
            recovery = encrypted_recovery_payload(cli, snapshot) if args.include_private else None
        if recovery is not None:
            encrypted_path = args.destination / ("p1pro-recovery-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json.gpg")
            encrypt_verified(json.dumps(recovery, sort_keys=True).encode(), password, encrypted_path)
            print(f"Encrypted, partial identity recovery snapshot saved: {encrypted_path}")
        target = write_snapshot(args.destination, snapshot)
        print(f"Public-only snapshot saved: {target}")
        print("This is not sufficient to restore device identity; no device writes were performed.")
        return 0
    except BackupError as error:
        print("STOP: " + str(error), file=sys.stderr)
    except KeyboardInterrupt:
        print("STOP: cancelled; no retry or device writes.", file=sys.stderr)
    except Exception:
        print("STOP: backup failed; device details suppressed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
