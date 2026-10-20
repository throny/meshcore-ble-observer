import importlib.util
import os
from pathlib import Path
import threading

import pytest


MODULE = Path(__file__).resolve().parents[2] / "tools/hardware/p1pro_usb_common.py"


def helper():
    spec = importlib.util.spec_from_file_location("p1pro_usb_common", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nonblocking_pipe_write_completes_without_duplicate_or_missing_bytes():
    common = helper()
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    expected = bytes(range(256)) * 4096
    actual = bytearray()

    def drain():
        while True:
            block = os.read(read_fd, 8192)
            if not block:
                return
            actual.extend(block)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        common.write_all(write_fd, expected, timeout=5)
    finally:
        os.close(write_fd)
        reader.join(timeout=5)
        os.close(read_fd)
    assert not reader.is_alive()
    assert actual == expected


def test_short_write_timeout_does_not_retry_or_duplicate_accepted_prefix():
    common = helper()
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    payload = b"synthetic" * 100000
    try:
        with pytest.raises(common.BackupError, match="no command was retried"):
            common.write_all(write_fd, payload, timeout=0.01)
        accepted = os.read(read_fd, 8192)
    finally:
        os.close(write_fd)
        os.close(read_fd)
    assert accepted
    assert payload.startswith(accepted)
