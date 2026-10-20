import sys
import unittest
from unittest.mock import patch
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from observer_build.release_resolver import (
    ReleaseMetadata,
    ReleaseResolutionError,
    resolve_official_latest_repeater_release,
    resolve_latest_repeater_release,
)


SHA_1171 = "d92964352441e53b93e8667b802e04f6e072b39e"
SHA_1110 = "a" * 40
SHA_119 = "b" * 40


class ReleaseResolverTests(unittest.TestCase):
    def test_legacy_letter_suffix_release_does_not_block_numeric_stable_selection(self):
        with patch("observer_build.release_resolver._github_json_with_headers", return_value=([
            {"tag_name": "repeater-v1.0.0c", "draft": False, "prerelease": False},
            {"tag_name": "repeater-v1.18.0", "draft": False, "prerelease": False},
        ], None)), patch("observer_build.release_resolver._resolve_tag_commit", return_value="1" * 40):
            self.assertEqual("repeater-v1.18.0", resolve_official_latest_repeater_release().tag)

    def test_only_selected_stable_tag_requires_a_tag_lookup(self):
        with patch("observer_build.release_resolver._github_json_with_headers", return_value=([
            {"tag_name": "repeater-v1.9", "draft": False, "prerelease": False},
            {"tag_name": "repeater-v1.10", "draft": False, "prerelease": False},
            {"tag_name": "repeater-v99.0", "draft": True, "prerelease": False},
        ], None)), patch("observer_build.release_resolver._resolve_tag_commit", return_value=SHA_1110) as lookup:
            self.assertEqual("repeater-v1.10", resolve_official_latest_repeater_release().tag)
        lookup.assert_called_once_with("https://api.github.com/repos/meshcore-dev/MeshCore", "repeater-v1.10")

    def test_selects_latest_numeric_stable_repeater_release(self):
        """Would fail if tags were sorted lexically rather than semantically."""
        pin = resolve_latest_repeater_release(
            [
                ReleaseMetadata("repeater-v1.9", False, False, SHA_119),
                ReleaseMetadata("repeater-v1.10", False, False, SHA_1110),
                ReleaseMetadata("repeater-v1.17.1", False, False, SHA_1171),
            ]
        )

        self.assertEqual("repeater-v1.17.1", pin.tag)
        self.assertEqual(SHA_1171, pin.commit_sha)

    def test_excludes_drafts_prereleases_and_other_release_roles(self):
        """Would fail if an ineligible release could become the selected source."""
        pin = resolve_latest_repeater_release(
            [
                ReleaseMetadata("repeater-v9.0", True, False, "c" * 40),
                ReleaseMetadata("repeater-v8.0", False, True, "d" * 40),
                ReleaseMetadata("companion-v99.0", False, False, "e" * 40),
                ReleaseMetadata("room-server-v99.0", False, False, "f" * 40),
                ReleaseMetadata("repeater-v1.10", False, False, SHA_1110),
            ]
        )

        self.assertEqual("repeater-v1.10", pin.tag)

    def test_rejects_a_candidate_without_a_full_immutable_sha(self):
        """Would fail if a mutable tag/ref name could be used as a build pin."""
        with self.assertRaisesRegex(ReleaseResolutionError, "full 40-character"):
            resolve_latest_repeater_release(
                [ReleaseMetadata("repeater-v1.18", False, False, "d929643")]
            )

    def test_rejects_empty_or_ineligible_release_results(self):
        """Would fail if the resolver silently fell back to an arbitrary branch."""
        with self.assertRaisesRegex(ReleaseResolutionError, "No eligible"):
            resolve_latest_repeater_release([])

        with self.assertRaisesRegex(ReleaseResolutionError, "No eligible"):
            resolve_latest_repeater_release(
                [ReleaseMetadata("companion-v1.18", False, False, "a" * 40)]
            )

    def test_design_time_v1171_pin_is_not_hard_coded_as_latest(self):
        """Would fail if the resolver returned the recorded design-time fixture forever."""
        pin = resolve_latest_repeater_release(
            [
                ReleaseMetadata("repeater-v1.17.1", False, False, SHA_1171),
                ReleaseMetadata("repeater-v1.18.0", False, False, "1" * 40),
            ]
        )

        self.assertEqual("repeater-v1.18.0", pin.tag)
        self.assertEqual("1" * 40, pin.commit_sha)

    def test_fetches_release_metadata_and_resolves_the_tag_to_a_commit(self):
        """Would fail if live selection trusted a mutable release target instead of its tag SHA."""
        responses = {
            "https://api.github.com/repos/meshcore-dev/MeshCore/releases?per_page=100": b'[{"tag_name":"repeater-v1.18.0","draft":false,"prerelease":false}]',
            "https://api.github.com/repos/meshcore-dev/MeshCore/git/ref/tags/repeater-v1.18.0": b'{"object":{"type":"commit","sha":"1111111111111111111111111111111111111111"}}',
        }

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def read(self):
                return self.payload

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                return False

        with patch("observer_build.release_resolver.urlopen", side_effect=lambda request, timeout: Response(responses[request.full_url])):
            pin = resolve_official_latest_repeater_release()

        self.assertEqual("repeater-v1.18.0", pin.tag)
        self.assertEqual("1" * 40, pin.commit_sha)

    def test_follows_pagination_and_annotated_tag_to_find_latest_release(self):
        """Would fail if a later release page or annotated tag object was ignored."""
        responses = {
            "https://api.github.com/repos/meshcore-dev/MeshCore/releases?per_page=100": (b'[{"tag_name":"repeater-v1.9","draft":false,"prerelease":false}]', '<https://api.github.com/repos/meshcore-dev/MeshCore/releases?per_page=100&page=2>; rel="next"'),
            "https://api.github.com/repos/meshcore-dev/MeshCore/releases?per_page=100&page=2": (b'[{"tag_name":"repeater-v1.10","draft":false,"prerelease":false}]', None),
            "https://api.github.com/repos/meshcore-dev/MeshCore/git/ref/tags/repeater-v1.9": (b'{"object":{"type":"commit","sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}', None),
            "https://api.github.com/repos/meshcore-dev/MeshCore/git/ref/tags/repeater-v1.10": (b'{"object":{"type":"tag","sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}', None),
            "https://api.github.com/repos/meshcore-dev/MeshCore/git/tags/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": (b'{"object":{"type":"commit","sha":"1111111111111111111111111111111111111111"}}', None),
        }
        class Response:
            def __init__(self, item): self.payload, self.link = item
            def read(self): return self.payload
            def getheader(self, name): return self.link if name == "Link" else None
            def __enter__(self): return self
            def __exit__(self, *unused): return False
        with patch("observer_build.release_resolver.urlopen", side_effect=lambda request, timeout: Response(responses[request.full_url])):
            pin = resolve_official_latest_repeater_release()
        self.assertEqual("repeater-v1.10", pin.tag)

    def test_rejects_malformed_official_release_records(self):
        """Would fail if malformed API metadata were silently treated as stable."""
        class Response:
            def read(self): return b'[{"tag_name":"repeater-v1.10","draft":"false","prerelease":false}]'
            def getheader(self, name): return None
            def __enter__(self): return self
            def __exit__(self, *unused): return False
        with patch("observer_build.release_resolver.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ReleaseResolutionError, "malformed"):
                resolve_official_latest_repeater_release()

    def test_rejects_malformed_repeater_version_tag(self):
        """Would fail if a malformed repeater version could be silently excluded or selected."""
        class Response:
            def read(self): return b'[{"tag_name":"repeater-v01.10","draft":false,"prerelease":false}]'
            def getheader(self, name): return None
            def __enter__(self): return self
            def __exit__(self, *unused): return False
        with patch("observer_build.release_resolver.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ReleaseResolutionError, "malformed repeater tag"):
                resolve_official_latest_repeater_release()
