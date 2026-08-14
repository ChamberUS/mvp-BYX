from __future__ import annotations

import json
import subprocess
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.provenance import capture_recorder_provenance
from adaptive_trader.microstructure.scientific_admission import (
    qualify_session,
    reject_duplicate_sessions,
)
from tests.microstructure.helpers import write_session


def test_git_commit_branch_and_clean_worktree_are_captured(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "tracked.txt").write_text("frozen\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")

    provenance = capture_recorder_provenance({"streams": ["depth"]}, repository=repository)

    assert len(provenance.software_commit) == 40
    assert provenance.branch == "main"
    assert provenance.dirty_worktree is False
    assert provenance.status == "COMPLETE"
    assert len(provenance.recorder_config_hash) == 64


def test_dirty_worktree_and_missing_git_metadata_are_explicit(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")
    (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    dirty = capture_recorder_provenance({}, repository=repository)
    unknown = capture_recorder_provenance({}, repository=tmp_path / "missing")

    assert dirty.dirty_worktree is True and dirty.status == "COMPLETE"
    assert unknown.software_commit == "UNKNOWN"
    assert unknown.dirty_worktree is None
    assert unknown.status == "PROVENANCE_INCOMPLETE"


def test_scientific_admission_accepts_clean_and_rejects_dirty_or_unknown(
    tmp_path: Path,
) -> None:
    session = write_session(tmp_path, market=MarketType.USD_M_FUTURES)
    manifest_path = session / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "software_commit": "a" * 40,
            "dirty_worktree": False,
            "branch": "main",
            "recorder_version": "PUBLIC_MICROSTRUCTURE_RECORDER_V1",
            "recorder_config_hash": "b" * 64,
            "provenance_status": "COMPLETE",
            "order_book_status": "SYNCHRONIZED",
            "recorder_runtime_health": {"dropped_events": 0},
            "liveness_incidents": [],
            "stream_delivery": [
                {
                    "requested_stream": stream,
                    "event_count": 1,
                    "first_exchange_event_time": payload["first_event"],
                    "last_exchange_event_time": payload["last_event"],
                }
                for stream in ("aggTrade", "bookTicker", "depth", "markPrice")
            ],
            "stream_liveness": {
                stream: {"state": "LIVE"}
                for stream in ("aggTrade", "bookTicker", "depth", "markPrice")
            },
        }
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    admitted = qualify_session(
        session, expected_market="USD_M_FUTURES", expected_symbol="ETHUSDT"
    )
    assert admitted.admitted is True
    assert admitted.replay_status == "DETERMINISTIC"

    payload["dirty_worktree"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    dirty = qualify_session(session, expected_market="USD_M_FUTURES", expected_symbol="ETHUSDT")
    assert dirty.admitted is False
    assert "DIRTY_WORKTREE" in dirty.reasons

    payload["dirty_worktree"] = False
    payload["software_commit"] = "UNKNOWN"
    payload["provenance_status"] = "PROVENANCE_INCOMPLETE"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    unknown = qualify_session(session, expected_market="USD_M_FUTURES", expected_symbol="ETHUSDT")
    assert unknown.admitted is False
    assert "PROVENANCE_INCOMPLETE" in unknown.reasons

    duplicate = reject_duplicate_sessions((admitted, admitted))
    assert duplicate[0].admitted is True
    assert duplicate[1].admitted is False
    assert "OVERLAPPING_DUPLICATE_SESSION" in duplicate[1].reasons


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=repository, check=True, capture_output=True)
