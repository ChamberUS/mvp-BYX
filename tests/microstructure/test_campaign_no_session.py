"""Capture lifecycle regression: rejected chunks are not scientific sessions."""

import json
import subprocess
from argparse import Namespace
from asyncio import run
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_trader.cli.main import _microstructure_campaign_record
from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.campaign import MicrostructureCampaignBuilder, load_campaign
from tests.microstructure.helpers import write_session


def arguments(tmp_path: Path) -> Namespace:
    return Namespace(
        chunk_seconds=60,
        total_seconds=60,
        streams="aggTrade,bookTicker,depth,markPrice",
        output_dir=tmp_path,
        campaign_id="no-session-regression",
        market="futures",
        symbol="ETHUSDT",
        maximum_reconnects=3,
    )


def admission(admitted: bool) -> SimpleNamespace:
    return SimpleNamespace(
        admitted=admitted,
        duration_seconds=60.0 if admitted else 0.0,
        as_dict=lambda: {"admitted": admitted, "reasons": "" if admitted else "DIRTY_WORKTREE"},
    )


def fake_recorder(monkeypatch: pytest.MonkeyPatch, sessions: list[tuple[Path, str]]) -> None:
    class Recorder:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run(self) -> SimpleNamespace:
            path, completeness = sessions.pop(0)
            return SimpleNamespace(
                session=SimpleNamespace(session_path=path, completeness=completeness)
            )

        def request_stop(self) -> None:
            pass

    monkeypatch.setattr("adaptive_trader.cli.main.PublicMicrostructureRecorder", Recorder)


def test_exact_empty_campaign_error_then_rejected_chunk_retries_valid_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(ValueError, match="^campaign requires at least one session$"):
        MicrostructureCampaignBuilder().build("no-session-regression", ())
    rejected = write_session(tmp_path / "rejected", market=MarketType.USD_M_FUTURES)
    valid = write_session(tmp_path / "valid", market=MarketType.USD_M_FUTURES)
    fake_recorder(monkeypatch, [(rejected, "COMPLETE"), (valid, "COMPLETE")])
    monkeypatch.setattr(
        "adaptive_trader.cli.main.qualify_session", lambda path, **kw: admission(path == valid)
    )
    delays = []
    manifest = tmp_path / "campaigns/no-session-regression/campaign_manifest.json"

    async def retry(awaitable, *, timeout):
        awaitable.close()
        delays.append(timeout)
        assert not manifest.exists()  # Never publishes an empty campaign as success.
        raise TimeoutError

    monkeypatch.setattr("adaptive_trader.cli.main.asyncio.wait_for", retry)
    assert run(_microstructure_campaign_record(arguments(tmp_path))) == 0
    assert delays == [10]
    campaign = load_campaign(manifest)
    assert len(campaign.sessions) == 1
    assert campaign.sessions[0].path == str(valid)
    shown = json.loads(capsys.readouterr().out)
    assert shown["success"] is True
    assert json.loads((rejected / "scientific_admission.json").read_text())["admitted"] is False
    previous = manifest.read_bytes()
    assert run(_microstructure_campaign_record(arguments(tmp_path))) == 0
    assert manifest.read_bytes() == previous  # Resume does not duplicate or rewrite valid state.


def test_no_complete_session_returns_retry_not_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    incomplete = write_session(
        tmp_path / "partial", market=MarketType.USD_M_FUTURES, complete=False
    )
    before = (incomplete / "manifest.json").read_bytes()
    fake_recorder(monkeypatch, [(incomplete, "INCOMPLETE")])
    assert run(_microstructure_campaign_record(arguments(tmp_path))) == 75
    shown = json.loads(capsys.readouterr().out)
    assert shown["capture_status"] == "NO_SESSION"
    assert shown["success"] is False
    assert not (tmp_path / "campaigns/no-session-regression/campaign_manifest.json").exists()
    assert (incomplete / "manifest.json").read_bytes() == before


def test_rejection_backoff_is_bounded_without_tight_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejected = write_session(tmp_path / "rejected", market=MarketType.USD_M_FUTURES)
    partial = write_session(tmp_path / "partial", market=MarketType.USD_M_FUTURES, complete=False)
    fake_recorder(monkeypatch, [(rejected, "COMPLETE")] * 8 + [(partial, "INCOMPLETE")])
    monkeypatch.setattr(
        "adaptive_trader.cli.main.qualify_session", lambda path, **kw: admission(False)
    )
    delays = []

    async def retry(awaitable, *, timeout):
        awaitable.close()
        delays.append(timeout)
        raise TimeoutError

    monkeypatch.setattr("adaptive_trader.cli.main.asyncio.wait_for", retry)
    assert run(_microstructure_campaign_record(arguments(tmp_path))) == 75
    assert delays == [10, 20, 30, 40, 50, 60, 60, 60]


def test_real_corruption_is_not_classified_as_no_session(tmp_path: Path) -> None:
    manifest = tmp_path / "campaigns/no-session-regression/campaign_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{corrupt")
    before = manifest.read_bytes()
    with pytest.raises(ValueError):
        run(_microstructure_campaign_record(arguments(tmp_path)))
    assert manifest.read_bytes() == before


@pytest.mark.parametrize(("exit_code", "delay"), [(0, 10), (75, 10), (2, 300)])
def test_supervisor_distinguishes_retry_from_corruption(exit_code: int, delay: int) -> None:
    script = Path(__file__).parents[2] / "scripts/continuous_capture.sh"
    result = subprocess.run(
        ["/bin/bash", str(script), "--retry-delay", str(exit_code)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(result.stdout.strip()) == delay


def test_stop_during_rejection_returns_no_session_promptly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio
    import signal

    rejected = write_session(tmp_path / "rejected", market=MarketType.USD_M_FUTURES)
    fake_recorder(monkeypatch, [(rejected, "COMPLETE")])

    async def capture():
        loop = asyncio.get_running_loop()
        handlers = {}
        monkeypatch.setattr(
            loop, "add_signal_handler", lambda sig, callback: handlers.update({sig: callback})
        )
        monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: handlers.pop(sig, None))

        def rejected_admission(path, **kwargs):
            handlers[signal.SIGTERM]()
            return admission(False)

        monkeypatch.setattr("adaptive_trader.cli.main.qualify_session", rejected_admission)
        return await _microstructure_campaign_record(arguments(tmp_path))

    assert run(capture()) == 75
    assert json.loads(capsys.readouterr().out)["capture_status"] == "NO_SESSION"


def test_existing_valid_campaign_incomplete_target_is_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    existing = write_session(tmp_path / "existing", market=MarketType.USD_M_FUTURES)
    partial = write_session(tmp_path / "partial", market=MarketType.USD_M_FUTURES, complete=False)
    builder = MicrostructureCampaignBuilder()
    manifest = tmp_path / "campaigns/no-session-regression/campaign_manifest.json"
    builder.write(builder.build("no-session-regression", (existing,)), manifest)
    before = manifest.read_bytes()
    fake_recorder(monkeypatch, [(partial, "INCOMPLETE")])
    monkeypatch.setattr(
        "adaptive_trader.cli.main.qualify_session",
        lambda path, **kwargs: SimpleNamespace(admitted=True, duration_seconds=30.0),
    )
    assert run(_microstructure_campaign_record(arguments(tmp_path))) == 75
    shown = json.loads(capsys.readouterr().out)
    assert shown["capture_status"] == "INCOMPLETE"
    assert shown["success"] is False
    assert len(shown["sessions"]) == 1
    assert manifest.read_bytes() == before
