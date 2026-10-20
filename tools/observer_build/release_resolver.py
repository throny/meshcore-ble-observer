"""Resolve immutable source pins from official MeshCore release metadata."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Sequence
from urllib.parse import quote
from urllib.request import Request, urlopen


_REPEATER_TAG = re.compile(r"^repeater-v((?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*))*)$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


class ReleaseResolutionError(ValueError):
    """Raised when official metadata cannot provide an immutable repeater pin."""


@dataclass(frozen=True)
class ReleaseMetadata:
    tag: str
    draft: bool
    prerelease: bool
    commit_sha: str | None


@dataclass(frozen=True)
class ReleasePin:
    tag: str
    commit_sha: str


def _version(tag: str) -> tuple[int, ...] | None:
    match = _REPEATER_TAG.fullmatch(tag)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def resolve_latest_repeater_release(releases: Sequence[ReleaseMetadata]) -> ReleasePin:
    """Select the newest stable ``repeater-v*`` release and require its full SHA.

    The caller must obtain ``commit_sha`` by resolving the annotated/lightweight
    release tag through the official Git API.  This function deliberately never
    substitutes a branch or a short hash.
    """
    candidates = [(metadata, _version(metadata.tag)) for metadata in releases]
    eligible = [item for item in candidates if not item[0].draft and not item[0].prerelease and item[1] is not None]
    if not eligible:
        raise ReleaseResolutionError("No eligible stable repeater-v* release was returned")

    selected, _ = max(eligible, key=lambda item: item[1])
    if not selected.commit_sha or not _FULL_SHA.fullmatch(selected.commit_sha):
        raise ReleaseResolutionError(
            f"Release {selected.tag} does not resolve to a full 40-character immutable commit SHA"
        )
    return ReleasePin(tag=selected.tag, commit_sha=selected.commit_sha.lower())


def _github_json_with_headers(url: str) -> tuple[object, str | None]:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "meshcore-observer-build"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8")), getattr(response, "getheader", lambda _name: None)("Link")
    except Exception as error:
        raise ReleaseResolutionError(f"Could not read official MeshCore release metadata: {error}") from error


def _github_json(url: str) -> object:
    return _github_json_with_headers(url)[0]


def _resolve_tag_commit(api_base: str, tag: str) -> str | None:
    reference = _github_json(f"{api_base}/git/ref/tags/{quote(tag, safe='')}")
    if not isinstance(reference, dict):
        return None
    target = reference.get("object")
    while isinstance(target, dict) and target.get("type") == "tag":
        annotated = _github_json(f"{api_base}/git/tags/{target.get('sha', '')}")
        target = annotated.get("object") if isinstance(annotated, dict) else None
    sha = target.get("sha") if isinstance(target, dict) and target.get("type") == "commit" else None
    return sha if isinstance(sha, str) else None


def resolve_official_latest_repeater_release(
    repository: str = "meshcore-dev/MeshCore",
) -> ReleasePin:
    """Fetch official GitHub metadata and pin the latest stable Repeater tag.

    A release's ``target_commitish`` is deliberately ignored: it can be a
    branch. The tag ref (and, when needed, annotated tag object) supplies the
    immutable commit used by the resolver.
    """
    api_base = f"https://api.github.com/repos/{repository}"
    url = f"{api_base}/releases?per_page=100"
    releases: list[object] = []
    while url:
        page, link = _github_json_with_headers(url)
        if not isinstance(page, list):
            raise ReleaseResolutionError("Official MeshCore releases response was not a list")
        releases.extend(page)
        match = re.search(r"<([^>]+)>;\s*rel=\"next\"", link or "")
        url = match.group(1) if match else ""
    metadata = []
    for release in releases:
        if not isinstance(release, dict):
            raise ReleaseResolutionError("Official MeshCore releases response contains a malformed record")
        if not isinstance(release.get("tag_name"), str) or type(release.get("draft")) is not bool or type(release.get("prerelease")) is not bool:
            raise ReleaseResolutionError("Official MeshCore releases response contains a malformed record")
        tag = release["tag_name"]
        # Historical letter-suffixed releases (e.g. v1.0.0c) are outside
        # the numeric stable policy, not candidates. Malformed numeric tags
        # still fail closed rather than being silently normalized.
        if re.fullmatch(r"repeater-v[\d.]+", tag) and _version(tag) is None:
            raise ReleaseResolutionError(f"Official MeshCore releases response contains malformed repeater tag {tag!r}")
        metadata.append(
            ReleaseMetadata(
                tag=tag,
                draft=release["draft"],
                prerelease=release["prerelease"],
                # Select first, then resolve only that tag. Old/draft tag lookups
                # waste the unauthenticated API quota and cannot affect selection.
                commit_sha="0" * 40,
            )
        )
    selected = resolve_latest_repeater_release(metadata)
    return resolve_latest_repeater_release([
        ReleaseMetadata(selected.tag, False, False, _resolve_tag_commit(api_base, selected.tag))
    ])
