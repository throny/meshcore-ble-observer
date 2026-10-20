"""Provenance written beside locally built observer firmware artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .release_resolver import ReleasePin


@dataclass(frozen=True)
class BuildManifest:
    release: ReleasePin
    patch_id: str
    patch_sha256: str
    baseline_environment: str
    custom_environment: str
    custom_build_flags: list[str]
    versions: dict[str, str]
    commands: dict[str, list[str] | None]
    timestamp: str
    artifacts: dict[str, str]
    memory: dict[str, dict[str, int] | None]
    patch_series: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        series = payload.pop("patch_series")
        payload["patch"] = {"id": payload.pop("patch_id"), "sha256": payload.pop("patch_sha256")}
        if series is not None:
            payload["patch"]["series"] = series
        payload["baseline"] = {"environment": payload.pop("baseline_environment")}
        payload["stock_comparison"] = None
        if self.commands.get("baseline") is not None:
            payload["stock_comparison"] = {
                "environment": payload["baseline"]["environment"],
                "command": self.commands["baseline"],
                "artifacts": {name: digest for name, digest in self.artifacts.items() if name.startswith("baseline.")},
                "memory": self.memory["baseline"],
            }
        payload["custom"] = {
            "environment": payload.pop("custom_environment"),
            "build_flags": payload.pop("custom_build_flags"),
        }
        return payload

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
