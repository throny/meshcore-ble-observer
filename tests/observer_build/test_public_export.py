import ast
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from public_export import PublicExportError, create_public_export, _is_allowed


PRIVATE_SNAPSHOT = (
    ROOT / ".superpowers/sdd/public-usability-preparation"
    / "private-preservation-2026-10-19/tools/hardware/p1pro_private_backup.py"
)


def _private_denylist():
    """Read actual operator identity tokens only from the ignored 0600 snapshot."""
    if not PRIVATE_SNAPSHOT.is_file():
        return None
    tree = ast.parse(PRIVATE_SNAPSHOT.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"EXPECTED_NAME", "EXPECTED_PREFIX"}:
                values[target.id] = ast.literal_eval(node.value)
    return tuple(values[key] for key in ("EXPECTED_NAME", "EXPECTED_PREFIX")) if len(values) == 2 else None


@contextmanager
def public_source_fixture():
    """Commit current portable sources in isolation, never the working history."""
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        paths = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            text=True,
        ).split("\0")
        for name in set(paths):
            if not name or not _is_allowed(name):
                continue
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        private = source / "docs/hardware-tests/private-fixture.md"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("Private fixture, deliberately outside the export allowlist.\n")
        for args in (["init", "--quiet"], ["add", "-A"], [
            "-c", "user.name=Export Test", "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "-m", "synthetic public source fixture",
        ]):
            subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
        yield source, Path(temporary) / "export"


class PublicExportTests(unittest.TestCase):
    def test_public_export_keeps_upstream_license_and_export_entrypoint(self):
        for name in ("license.txt", "tools/public_export.py", "tests/observer_build/test_public_export.py"):
            self.assertTrue(_is_allowed(name), name)

    def test_public_distribution_includes_broker_presets_needed_by_setup(self):
        for name in ("meshmapper.toml", "letsmesh.toml"):
            self.assertTrue(_is_allowed("observer/meshcoretomqtt/presets/" + name))

    def test_clean_export_has_a_new_root_and_only_allowlisted_paths(self):
        with public_source_fixture() as (source, output):
            result = create_public_export(source, output)
            self.assertEqual("clean-public-export", result["kind"])
            self.assertTrue((output / "README.md").is_file())
            self.assertTrue((output / "CONTRIBUTING.md").is_file())
            self.assertTrue((output / ".github/workflows/observer-ble.yml").is_file())
            tracked = subprocess.run(
                ["git", "-C", str(output), "ls-files"], check=True,
                capture_output=True, text=True,
            ).stdout.splitlines()
            self.assertEqual(set(result["files"]), set(tracked) - {"PUBLIC-EXPORT-MANIFEST.json"})
            self.assertFalse(any(path.startswith(("docs/hardware-tests/", ".superpowers/")) for path in tracked))
            self.assertNotIn("tools/hardware/p1pro_private_backup.py", tracked)
            self.assertNotIn("tests/hardware/test_p1pro_private_backup.py", tracked)
            self.assertEqual("", subprocess.run(
                ["git", "-C", str(output), "remote"], check=True,
                capture_output=True, text=True,
            ).stdout)
            history = subprocess.run(
                ["git", "-C", str(output), "rev-list", "--count", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual("1", history)

    def test_public_export_contains_no_identity_tokens_from_local_private_denylist(self):
        denylist = _private_denylist()
        if denylist is None:
            self.skipTest("private source snapshot is unavailable in a clean checkout")
        with public_source_fixture() as (source, output):
            create_public_export(source, output)
            blobs = [p.read_bytes() for p in output.rglob("*")
                     if p.is_file() and ".git" not in p.parts]
            for sensitive in denylist:
                token = sensitive.encode("utf-8")
                if any(token in blob for blob in blobs):
                    raise AssertionError("private identity denylist match in public export")

    def test_export_refuses_uncommitted_allowlisted_changes(self):
        with public_source_fixture() as (source, output):
            (source / "tools/public_export.py").write_text("# uncommitted fixture\n")
            with self.assertRaisesRegex(PublicExportError, "uncommitted"):
                create_public_export(source, output)

    def test_export_refuses_workspace_destinations_symlinks_and_nonempty_targets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            link = root / "linked"
            target = root / "target"
            target.mkdir(mode=0o700)
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(PublicExportError):
                create_public_export(ROOT, link)
            with self.assertRaises(PublicExportError):
                create_public_export(ROOT, ROOT / "public-export-inside-repo")
            occupied = root / "occupied"
            occupied.mkdir(mode=0o700)
            marker = occupied / "keep"
            marker.write_text("retain")
            with self.assertRaises(PublicExportError):
                create_public_export(ROOT, occupied)
            self.assertEqual("retain", marker.read_text())
