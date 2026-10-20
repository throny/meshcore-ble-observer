"""Portable, read-only P1-Pro USB CLI framing and encrypted-output primitives.

No device is opened and no command is sent at import time.
"""

import fcntl
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import tempfile
import termios
import time


class BackupError(Exception):
    """Safe user-facing error; never embed device replies or secret values."""


SUPPORTED_REPEATER_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9][A-Za-z0-9.+_-]*)?"
    r"(?: \(Build: [^()\r\n]{1,64}\))?$"
)


def write_all(fd, data, timeout):
    """Finish one already-authorized frame without resending any accepted bytes."""
    deadline = time.monotonic() + timeout
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(fd, view[offset:])
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                raise BackupError("Serial command write timed out; no command was retried.") from None
            continue
        except OSError:
            raise BackupError("Serial command write failed; no command was retried.") from None
        if written <= 0:
            raise BackupError("Serial command write stopped early; no command was retried.")
        offset += written


class SerialCLI:
    """Stock CR-terminated command framing guarded by a changing-identity sentinel."""

    def __init__(self, port, timeout=4, allowed_commands=()):
        self.port = str(port)
        self.timeout = timeout
        self.allowed_commands = frozenset(allowed_commands)
        self.fd = None
        self.buffer = b""
        self.public_key = None

    def __enter__(self):
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = (attrs[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB |
                                     termios.HUPCL | termios.CRTSCTS))
            attrs[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[4] = attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            return self
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _line(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise BackupError("Serial reply timed out; nothing was retried.")
            data = os.read(self.fd, 4096)
            if not data:
                raise BackupError("Serial device disconnected.")
            self.buffer += data
            if len(self.buffer) > 65536:
                raise BackupError("Serial reply exceeded the safety bound.")
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line.rstrip(b"\r").decode("utf-8", errors="strict")

    def query(self, command):
        if command not in self.allowed_commands and not re.fullmatch(r"sensor list [0-9]{1,3}", command):
            raise BackupError("Command is outside the read-only allowlist.")
        data = (command + "\rget public.key\r").encode("ascii")
        write_all(self.fd, data, self.timeout)
        deadline = time.monotonic() + self.timeout
        state = "echo"
        lines = []
        total = 0
        while True:
            line = self._line(deadline)
            total += len(line)
            if total > 65536:
                raise BackupError("Serial transaction exceeded the safety bound.")
            if state == "echo":
                if line == command:
                    state = "reply"
            elif state == "reply":
                if line == "get public.key":
                    state = "sentinel"
                else:
                    lines.append(line)
            else:
                match = re.fullmatch(r"  -> > ([0-9a-fA-F]{64})", line)
                if match:
                    public_key = match.group(1).lower()
                    if self.public_key is not None and self.public_key != public_key:
                        raise BackupError("Device public identity changed during capture.")
                    self.public_key = public_key
                    return "\n".join(lines)


def value(reply):
    if not reply.startswith("  -> "):
        raise BackupError("Expected CLI reply marker is missing.")
    return reply[5:].removeprefix("> ").strip()


def validate_destination(directory, repo_root=None):
    directory = Path(directory).absolute()
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    for path in (directory, *directory.parents):
        if path.is_symlink():
            raise BackupError("Backup directory must not have symlink components.")
    resolved = directory.resolve()
    root = root.resolve()
    if resolved == root or root in resolved.parents:
        raise BackupError("Backup destination must be outside the repository.")
    try:
        info = directory.stat()
    except OSError:
        raise BackupError("Backup destination is unavailable.") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise BackupError("Backup directory must be caller-owned with mode 0700.")


def gpg_bytes(data, password, home, output=None, max_output=None):
    read_fd, write_fd = os.pipe()
    try:
        secret = password.encode("utf-8") + b"\n"
        if len(secret) > 1024 or b"\n" in secret[:-1] or b"\r" in secret:
            raise BackupError("Encryption password has unsupported length or line breaks.")
        os.write(write_fd, secret)
        os.close(write_fd)
        write_fd = None
        args = ["gpg", "--no-options", "--homedir", str(home), "--batch",
                "--no-symkey-cache", "--pinentry-mode", "loopback",
                "--passphrase-fd", str(read_fd), "--output", "-"]
        args += (["--symmetric", "--cipher-algo", "AES256", "--compress-algo", "none"]
                 if output is not None else ["--decrypt"])
        if max_output is not None:
            args += ["--max-output", str(max_output)]
        try:
            result = subprocess.run(args, input=data, stdout=output if output is not None else subprocess.PIPE,
                                    stderr=subprocess.PIPE, pass_fds=(read_fd,), timeout=60)
        except subprocess.TimeoutExpired:
            raise BackupError("GPG timed out; diagnostic output suppressed.") from None
        if result.returncode:
            raise BackupError("GPG operation failed; diagnostic output suppressed to protect secrets.")
        return result.stdout
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def encrypt_verified(payload, password, target):
    target = Path(target)
    validate_destination(target.parent)
    if target.exists() or target.is_symlink():
        raise BackupError("Refusing to overwrite an existing backup path.")
    fd, partial = tempfile.mkstemp(prefix=".encrypted-", suffix=".partial", dir=target.parent)
    try:
        with tempfile.TemporaryDirectory(prefix=".gpg-", dir=target.parent) as home:
            with os.fdopen(fd, "wb") as output:
                gpg_bytes(payload, password, home, output=output)
                output.flush()
                os.fsync(output.fileno())
            if gpg_bytes(Path(partial).read_bytes(), password, home) != payload:
                raise BackupError("Encrypted backup did not pass in-memory readback verification.")
        os.link(partial, target)
        os.chmod(target, 0o600, follow_symlinks=False)
    finally:
        Path(partial).unlink(missing_ok=True)


def verify_usb(port):
    if not re.fullmatch(r"/dev/ttyACM[0-9]+", str(port)):
        raise BackupError("Expected an explicit /dev/ttyACM device path.")
    device = (Path("/sys/class/tty") / Path(port).name / "device").resolve()
    for usb in (device, *device.parents):
        if (usb / "idVendor").exists():
            if ((usb / "idVendor").read_text().strip() == "2886" and
                    (usb / "idProduct").read_text().strip() == "0059"):
                return
            break
    raise BackupError("Expected P1-Pro repeater application USB identity is absent.")
