from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from adaptive_trader.cli.main import _parser, main
from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.foundation import (
    ARTIFACT_NAMES,
    MicrostructureFoundationService,
)
from adaptive_trader.microstructure.live import (
    PublicMicrostructureRecorder,
    PublicWebSocketConnection,
)
from adaptive_trader.microstructure.models import MicrostructureStreamType
from tests.microstructure.helpers import at, snapshot_event, write_session


def _all_option_strings(parser: argparse.ArgumentParser) -> set[str]:
    found: set[str] = set()
    pending = [parser]
    while pending:
        current = pending.pop()
        for action in current._actions:
            found.update(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                pending.extend(action.choices.values())
    return found


def test_foundation_replay_creates_exactly_eleven_non_financial_artifacts(
    tmp_path: Path,
) -> None:
    session = write_session(tmp_path / "capture")
    report = MicrostructureFoundationService().run(
        session_path=session,
        output_dir=tmp_path / "reports",
    )

    assert tuple(sorted(path.name for path in report.iterdir())) == tuple(sorted(ARTIFACT_NAMES))
    assert len(ARTIFACT_NAMES) == 11
    manifest = json.loads((report / "experiment_manifest.json").read_text(encoding="utf-8"))
    replay = json.loads((report / "replay_determinism.json").read_text(encoding="utf-8"))
    alpha = json.loads((report / "alpha_signal_summary.json").read_text(encoding="utf-8"))
    markdown = (report / "microstructure_foundation_report.md").read_text(encoding="utf-8")

    assert manifest["sprint"] == "4A.1"
    assert manifest["financial_candidate_assessment_generated"] is False
    assert manifest["authentication_used"] is False
    assert manifest["orders_sent"] is False
    assert manifest["leverage"] == "1"
    assert replay["same_input_hash"] and replay["same_output_hash"]
    assert replay["real_sleep_used"] is False
    assert alpha["thresholds_selected_by_pnl"] is False
    assert alpha["frequency"]["target_is_diagnostic_only"] is True
    assert "does not claim profitability" in markdown
    assert not (report / "candidate_assessment.json").exists()


def test_foundation_can_diagnose_long_or_short_independently(tmp_path: Path) -> None:
    session = write_session(tmp_path / "capture", market=MarketType.USD_M_FUTURES)
    long_report = MicrostructureFoundationService().run(
        session_path=session,
        output_dir=tmp_path / "long",
        models=("long",),
    )
    short_report = MicrostructureFoundationService().run(
        session_path=session,
        output_dir=tmp_path / "short",
        models=("short",),
    )
    long_manifest = json.loads(
        (long_report / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    short_manifest = json.loads(
        (short_report / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    assert long_manifest["alpha_models"] == ["long"]
    assert short_manifest["alpha_models"] == ["short"]
    with pytest.raises(ValueError, match="only long"):
        MicrostructureFoundationService().run(
            session_path=session,
            output_dir=tmp_path / "bad",
            models=("inverse",),
        )


def test_microstructure_cli_doctor_inspect_replay_and_alpha_are_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "cli.sqlite3"))
    session = write_session(tmp_path / "capture")

    assert main(["market", "microstructure", "doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["public_only"] is True
    assert doctor["authentication_available"] is False
    assert doctor["order_submission_available"] is False

    assert main(["market", "microstructure", "inspect", "--session", str(session)]) == 0
    assert json.loads(capsys.readouterr().out)["hashes_valid"] is True

    assert (
        main(
            [
                "research",
                "microstructure",
                "replay",
                "--session",
                str(session),
                "--speed",
                "max",
                "--output-dir",
                str(tmp_path / "replay"),
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["replay"]["real_sleep_used"] is False
    assert Path(replay["report_directory"]).is_dir()

    assert (
        main(
            [
                "research",
                "microstructure",
                "alpha-diagnose",
                "--session",
                str(session),
                "--models",
                "long",
                "--output-dir",
                str(tmp_path / "alpha"),
            ]
        )
        == 0
    )
    alpha = json.loads(capsys.readouterr().out)
    assert alpha["models"] == ["long"]
    assert alpha["thresholds_selected_by_pnl"] is False


def test_cli_rejects_invalid_market_stream_speed_models_and_has_no_auth_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "cli.sqlite3"))
    options = _all_option_strings(_parser())
    assert "--api-key" not in options
    assert "--secret" not in options
    assert "--listen-key" not in options
    assert "--order-endpoint" not in options
    with pytest.raises(SystemExit):
        main(
            [
                "market",
                "microstructure",
                "record",
                "--market",
                "margin",
                "--streams",
                "depth",
                "--output-dir",
                str(tmp_path),
            ]
        )
    with pytest.raises(SystemExit):
        main(
            [
                "market",
                "microstructure",
                "record",
                "--market",
                "spot",
                "--streams",
                "depth",
                "--depth-speed",
                "1s",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert (
        main(
            [
                "market",
                "microstructure",
                "record",
                "--market",
                "spot",
                "--streams",
                "depth,private",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "unsupported public streams" in capsys.readouterr().err


class _FakeConnection:
    urls: list[str] = []

    def __init__(self, url: str) -> None:
        self.urls.append(url)
        self.update_id = 100

    async def connect(self) -> None:
        return None

    async def receive_json(self) -> object:
        self.update_id += 1
        return {
            "e": "depthUpdate",
            "E": int(at(self.update_id - 90).timestamp() * 1000),
            "s": "ETHUSDT",
            "U": self.update_id,
            "u": self.update_id,
            "b": [["2000", "3"]],
            "a": [],
        }

    async def close(self) -> None:
        return None


def test_public_recorder_lifecycle_with_fake_transport_has_no_auth_or_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaptive_trader.microstructure import live

    ticks = iter((0.0, 0.1, 0.2, 2.0, 2.0))
    monkeypatch.setattr(live, "PublicWebSocketConnection", _FakeConnection)

    async def fake_snapshot(self: PublicMicrostructureRecorder):
        return snapshot_event(market=self.market_type)

    monkeypatch.setattr(PublicMicrostructureRecorder, "_snapshot_event", fake_snapshot)
    recorder = PublicMicrostructureRecorder(
        market_type=MarketType.SPOT,
        symbol="ETHUSDT",
        streams=("aggTrade", "bookTicker", "depth"),
        output_dir=tmp_path,
        duration_seconds=1,
        monotonic_clock=lambda: next(ticks),
    )
    result = asyncio.run(recorder.run())

    assert result.session.completeness == "COMPLETE"
    assert result.session.event_count == 4
    assert result.public_only is True
    assert result.authentication_used is False
    assert result.orders_sent is False
    assert result.order_book_status == "SYNCHRONIZED"
    assert "ethusdt@depth@100ms" in _FakeConnection.urls[-1]


def test_public_transport_and_recorder_validate_public_contracts() -> None:
    with pytest.raises(ValueError, match="positive"):
        PublicMicrostructureRecorder(
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            streams=("depth",),
            output_dir=Path("data"),
            duration_seconds=0,
        )
    with pytest.raises(ValueError, match="Futures-only"):
        PublicMicrostructureRecorder(
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            streams=("depth", "markPrice"),
            output_dir=Path("data"),
        )
    with pytest.raises(ValueError, match="requires depth"):
        PublicMicrostructureRecorder(
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            streams=("aggTrade",),
            output_dir=Path("data"),
        )
    recorder = PublicMicrostructureRecorder(
        market_type=MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        streams=("depth", "markPrice"),
        output_dir=Path("data"),
    )
    assert "ethusdt@markPrice@1s" in recorder._stream_url()
    assert "ethusdt@depth@100ms" in recorder._stream_url()
    assert snapshot_event().stream_type is MicrostructureStreamType.SNAPSHOT

    async def invalid_scheme() -> None:
        await PublicWebSocketConnection("https://example.com").connect()

    with pytest.raises(ValueError, match="only public wss"):
        asyncio.run(invalid_scheme())


class _CloseNotifyWriter:
    def write(self, payload: bytes) -> None:
        assert payload

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        raise OSError("APPLICATION_DATA_AFTER_CLOSE_NOTIFY")


def test_public_websocket_close_tolerates_tls_close_notify_race() -> None:
    connection = PublicWebSocketConnection("wss://example.com/stream")
    connection._writer = _CloseNotifyWriter()
    asyncio.run(connection.close())
    assert connection._writer is None
