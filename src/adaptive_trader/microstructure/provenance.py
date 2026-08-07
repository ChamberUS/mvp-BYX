"""Immutable recorder provenance captured before a public market-data session starts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

RECORDER_VERSION = "PUBLIC_MICROSTRUCTURE_RECORDER_V1"


@dataclass(frozen=True, slots=True)
class RecorderProvenance:
    software_commit: str
    dirty_worktree: bool | None
    branch: str
    recorder_version: str
    recorder_config_hash: str
    status: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def recorder_config_hash(config: dict[str, object]) -> str:
    """Hash only the effective, pre-capture recorder configuration."""

    payload = {"recorder_version": RECORDER_VERSION, **config}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def capture_recorder_provenance(
    config: dict[str, object], *, repository: Path | None = None
) -> RecorderProvenance:
    config_hash = recorder_config_hash(config)
    try:
        root = _git(("rev-parse", "--show-toplevel"), repository)
        commit = _git(("rev-parse", "HEAD"), Path(root))
        branch = _git(("branch", "--show-current"), Path(root)) or "DETACHED"
        dirty = bool(_git(("status", "--porcelain", "--untracked-files=normal"), Path(root)))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return RecorderProvenance(
            software_commit="UNKNOWN",
            dirty_worktree=None,
            branch="UNKNOWN",
            recorder_version=RECORDER_VERSION,
            recorder_config_hash=config_hash,
            status="PROVENANCE_INCOMPLETE",
        )
    return RecorderProvenance(
        software_commit=commit,
        dirty_worktree=dirty,
        branch=branch,
        recorder_version=RECORDER_VERSION,
        recorder_config_hash=config_hash,
        status="COMPLETE",
    )


def _git(arguments: tuple[str, ...], repository: Path | None) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
