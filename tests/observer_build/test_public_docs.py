from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observer_build.bridge_candidate import _privacy_findings


class PublicDocumentationTests(unittest.TestCase):
    def test_public_setup_docs_are_generic_and_all_relative_links_resolve(self):
        docs = {
            "docs/observer/quickstart.md": False,
            "docs/observer/bridge-setup.md": False,
            "docs/observer/upstream-contributions.md": False,
            # This file is copied to root README.md in the clean export.
            "docs/observer/public-export-readme.md": True,
        }
        for name, root_relative in docs.items():
            source = ROOT / name
            text = source.read_text(encoding="utf-8")
            self.assertFalse(_privacy_findings(text), name)
            base = ROOT if root_relative else source.parent
            for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
                if "://" in target or target.startswith(("mailto:", "#")):
                    continue
                self.assertTrue((base / target).resolve().is_file(), (name, target))

    def test_hardware_scope_and_upstream_submission_boundaries_are_explicit(self):
        host = (ROOT / "docs/observer/bridge-setup.md").read_text(encoding="utf-8")
        contribution = (ROOT / "docs/observer/upstream-contributions.md").read_text(encoding="utf-8")
        self.assertIn("P1-Pro", host)
        self.assertIn("experimental", host)
        self.assertIn("does not currently provide a supported one-command BLE installer", host)
        self.assertIn("inherited upstream installer and shared serial unit are **not** BLE-hardened", host)
        self.assertIn("Any service installation, enable/start, pairing change, production configuration, and rollback", host)
        self.assertIn("Do not publish", contribution)
        self.assertIn("clean upstream", contribution)
        self.assertIn("No PR has been submitted", contribution)
