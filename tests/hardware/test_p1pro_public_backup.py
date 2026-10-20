import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest


MODULE = Path(__file__).resolve().parents[2] / "tools/hardware/p1pro_public_backup.py"
PUBLIC = "12" * 32
SYNTHETIC_PRIVATE_KEY = bytes(range(1, 65)).hex()


def helper():
    sys.path.insert(0, str(MODULE.parent))
    spec = importlib.util.spec_from_file_location("public_backup", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCLI:
    def __init__(self, board="Seeed SenseCap Solar", public=PUBLIC, private=SYNTHETIC_PRIVATE_KEY):
        self.public_key = public
        self.board = board
        self.private = private
        self.calls = []

    def query(self, command):
        self.calls.append(command)
        values = {
            "ver": "v88.77.66-test (Build: 01 Jan 2000)",
            "board": self.board,
            "get name": "> repeater",
            "get public.key": "> " + self.public_key,
            "get prv.key": "> " + self.private,
        }
        return "  -> " + values.get(command, "> value")


def test_public_snapshot_excludes_private_identity_and_sensitive_getters():
    h = helper()
    cli = FakeCLI()
    result = h.capture(cli)
    assert result["identity"] == {"public_key": PUBLIC}
    assert result["identity_recovery_included"] is False
    assert result["complete_backup"] is False
    assert "get prv.key" not in cli.calls
    assert "get acl" not in cli.calls
    assert not {"get lat", "get lon", "get guest.password", "get owner.info"} & set(cli.calls)
    assert any("No private key" in gap for gap in result["coverage_gaps"])


def test_public_helper_is_independent_of_operator_specific_recovery_source():
    source = MODULE.read_text()
    assert "p1pro_private_backup" not in source
    assert "DEFAULT_IDENTITY_RECORD" not in source
    assert "EXPECTED_NAME" not in source
    assert "/home/" not in source


def test_main_disables_core_dumps_before_any_recovery_input(monkeypatch, tmp_path):
    import resource

    h = helper()
    requested = []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, limits: requested.append((kind, limits)))
    monkeypatch.setattr(sys, "argv", ["backup", "--port", "invalid", "--destination", str(tmp_path), "--include-private"])
    old_umask = os.umask(0o077)
    try:
        assert h.main() == 1  # Stops at port validation, without touching a device.
    finally:
        os.umask(old_umask)
    assert requested == [(resource.RLIMIT_CORE, (0, 0))]


def test_wrong_board_fails_closed():
    h = helper()
    with pytest.raises(h.BackupError):
        h.capture(FakeCLI(board="Companion"))


def test_optional_full_public_identity_pin_is_enforced_before_settings():
    h = helper()
    cli = FakeCLI()
    with pytest.raises(h.BackupError):
        h.capture(cli, expected_public_key="34" * 32)
    assert "get radio" not in cli.calls


def test_private_recovery_payload_is_separate_and_validates_key_without_echoing():
    h = helper()
    cli = FakeCLI()
    public = h.capture(cli)
    payload = h.encrypted_recovery_payload(cli, public)
    assert payload["identity"]["private_key"] == SYNTHETIC_PRIVATE_KEY
    assert "private_key" not in public["identity"]
    assert payload["identity_recovery_included"] is True
    assert payload["restore_validated"] is False
    bad = FakeCLI(private="secret-invalid")
    with pytest.raises(h.BackupError) as error:
        h.encrypted_recovery_payload(bad, public)
    assert "secret-invalid" not in str(error.value)


@pytest.mark.skipif(not shutil.which("gpg"), reason="GPG is required")
def test_encrypted_recovery_payload_round_trips_without_plaintext_file(tmp_path):
    h = helper()
    from p1pro_usb_common import gpg_bytes
    destination = tmp_path / "private"
    destination.mkdir(mode=0o700)
    cli = FakeCLI()
    snapshot = h.capture(cli)
    payload = h.encrypted_recovery_payload(cli, snapshot)
    data = json.dumps(payload).encode()
    target = destination / "recovery.json.gpg"
    h.encrypt_verified(data, "disposable-test-password", target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert SYNTHETIC_PRIVATE_KEY.encode() not in target.read_bytes()
    assert gpg_bytes(target.read_bytes(), "disposable-test-password", tmp_path) == data


def test_public_key_change_during_capture_fails_closed():
    h = helper()

    class Changed(FakeCLI):
        def query(self, command):
            result = super().query(command)
            if command == "get radio":
                self.public_key = "34" * 32
            return result

    with pytest.raises(h.BackupError):
        h.capture(Changed())


def test_output_is_private_and_refuses_existing_path(tmp_path):
    h = helper()
    destination = tmp_path / "private"
    destination.mkdir(mode=0o700)
    target = h.write_snapshot(destination, {"identity": {"public_key": PUBLIC}})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["identity"]["public_key"] == PUBLIC
    with pytest.raises(FileExistsError):
        h.write_snapshot(destination, {})


def test_output_directory_rejects_symlinks_and_repository(tmp_path):
    h = helper()
    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    h.validate_directory(safe, repo_root=tmp_path / "repo")
    link = tmp_path / "link"
    link.symlink_to(safe, target_is_directory=True)
    with pytest.raises(h.BackupError):
        h.validate_directory(link, repo_root=tmp_path / "repo")
    with pytest.raises(h.BackupError):
        h.validate_directory(tmp_path / "repo", repo_root=tmp_path / "repo")
