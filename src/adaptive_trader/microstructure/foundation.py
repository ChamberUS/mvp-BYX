"""Offline replay diagnostics and the fixed Sprint 4A.1 artifact contract."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from statistics import median

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.alpha import (
    GateContext,
    IntradayAlphaCoordinator,
    LongAlphaConfig,
    LongMicrostructureAlpha,
    NoTradeGate,
    NoTradeGateConfig,
    ShortAlphaConfig,
    ShortMicrostructureAlpha,
    summarize_alpha_frequency,
)
from adaptive_trader.microstructure.connection import stream_capabilities
from adaptive_trader.microstructure.features import (
    MicrostructureFeatureEngine,
    MicrostructureFeatureSnapshot,
)
from adaptive_trader.microstructure.models import (
    AlphaDecisionStatus,
    CalibrationStatus,
    IntradayAlphaDecision,
    LiquiditySnapshot,
    MicrostructureEvent,
    MicrostructureStreamType,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine, ReplaySpeed
from adaptive_trader.microstructure.storage import inspect_session

ARTIFACT_NAMES = (
    "experiment_manifest.json",
    "stream_capabilities.json",
    "capture_integrity.json",
    "order_book_integrity.json",
    "liquidity_summary.json",
    "feature_summary.json",
    "alpha_signal_summary.json",
    "no_trade_summary.json",
    "elastic_exit_synthetic_results.json",
    "replay_determinism.json",
    "microstructure_foundation_report.md",
)


class MicrostructureFoundationService:
    def run(
        self,
        *,
        session_path: Path,
        output_dir: Path,
        models: tuple[str, ...] = ("long", "short"),
    ) -> Path:
        if not models or set(models) - {"long", "short"}:
            raise ValueError("foundation models must contain only long and/or short")
        started = time.monotonic()
        started_at = datetime.now(tz=UTC)
        capture = inspect_session(session_path)
        market = MarketType(_required_string(capture, "market"))
        symbol = _required_string(capture, "symbol")
        manifest_hash = hashlib.sha256(
            (session_path / "manifest.json").read_bytes()
        ).hexdigest()
        experiment_id = (
            f"microstructure-foundation-{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{manifest_hash[:8]}"
        )
        target = output_dir / experiment_id
        target.mkdir(parents=True, exist_ok=False)
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        diagnostics = self._diagnose(events, market, symbol, models)
        max_replay = MicrostructureReplayEngine(seed=42).run(
            session_path,
            speed=ReplaySpeed.MAX,
            handler=lambda event, clock: event.event_id,
        )
        step_replay = MicrostructureReplayEngine(seed=42).run(
            session_path,
            speed=ReplaySpeed.STEP,
            handler=lambda event, clock: event.event_id,
        )
        replay = {
            "max": max_replay,
            "step": step_replay,
            "same_input_hash": max_replay.input_hash == step_replay.input_hash,
            "same_output_hash": max_replay.output_hash == step_replay.output_hash,
            "real_sleep_used": False,
            "virtual_clock_only": True,
        }
        capabilities = stream_capabilities(market, symbol)
        completed_at = datetime.now(tz=UTC)
        manifest = {
            "experiment_id": experiment_id,
            "sprint": "4A.1",
            "research_only": True,
            "financial_candidate_assessment_generated": False,
            "profitability_claimed": False,
            "network_used_for_replay": False,
            "authentication_used": False,
            "orders_sent": False,
            "paper_trading_enabled": False,
            "testnet_enabled": False,
            "leverage": "1",
            "session": str(session_path),
            "session_manifest_hash": manifest_hash,
            "alpha_models": models,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": Decimal(str(time.monotonic() - started)),
            "artifacts": ARTIFACT_NAMES,
        }
        self._write_json(target / "experiment_manifest.json", manifest)
        self._write_json(target / "stream_capabilities.json", capabilities)
        self._write_json(target / "capture_integrity.json", capture)
        self._write_json(target / "order_book_integrity.json", diagnostics["book"])
        self._write_json(target / "liquidity_summary.json", diagnostics["liquidity"])
        self._write_json(target / "feature_summary.json", diagnostics["features"])
        self._write_json(target / "alpha_signal_summary.json", diagnostics["alpha"])
        self._write_json(target / "no_trade_summary.json", diagnostics["no_trade"])
        self._write_json(
            target / "elastic_exit_synthetic_results.json",
            self._elastic_contract(),
        )
        self._write_json(target / "replay_determinism.json", replay)
        (target / "microstructure_foundation_report.md").write_text(
            self._markdown(manifest, diagnostics, capture),
            encoding="utf-8",
        )
        observed = tuple(sorted(path.name for path in target.iterdir()))
        if observed != tuple(sorted(ARTIFACT_NAMES)):
            raise RuntimeError("microstructure foundation artifact contract changed")
        return target

    def _diagnose(
        self,
        events: tuple[MicrostructureEvent, ...],
        market: MarketType,
        symbol: str,
        models: tuple[str, ...],
    ) -> dict[str, object]:
        book = LocalOrderBook(market, symbol)
        features = MicrostructureFeatureEngine()
        gate = NoTradeGate(
            NoTradeGateConfig(
                maximum_event_age_ms=Decimal("1000"),
                maximum_book_age_ms=Decimal("1000"),
                maximum_spread_bps=Decimal("10"),
                minimum_top_20_notional=Decimal("1"),
            )
        )
        long = LongMicrostructureAlpha(
            gate,
            LongAlphaConfig(
                maximum_spread_bps=Decimal("5"),
                minimum_bid_notional=Decimal("1"),
                minimum_depth_imbalance=Decimal("0.05"),
                minimum_ofi=Decimal("0"),
                minimum_trade_imbalance=Decimal("0.05"),
                minimum_microprice_edge_bps=Decimal("0"),
                minimum_momentum_bps=Decimal("0"),
                minimum_persistence_ms=100,
            ),
        )
        short = ShortMicrostructureAlpha(
            gate,
            ShortAlphaConfig(
                maximum_spread_bps=Decimal("6"),
                minimum_ask_notional=Decimal("1"),
                maximum_depth_imbalance=Decimal("-0.04"),
                maximum_ofi=Decimal("0"),
                maximum_trade_imbalance=Decimal("-0.04"),
                maximum_microprice_edge_bps=Decimal("0"),
                maximum_momentum_bps=Decimal("0"),
                minimum_persistence_ms=120,
            ),
        )
        liquidity_rows: list[LiquiditySnapshot] = []
        feature_rows: list[MicrostructureFeatureSnapshot] = []
        decisions: list[IntradayAlphaDecision] = []
        for event in events:
            if event.stream_type is MicrostructureStreamType.AGG_TRADE:
                features.record_event(event)
                continue
            if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                result = (
                    book.apply_update(event)
                    if book.synchronized
                    else book.buffer_update(event)
                )
                if not result.synchronized:
                    continue
            elif event.stream_type is MicrostructureStreamType.SNAPSHOT:
                result = book.apply_snapshot(event)
                if not result.synchronized:
                    continue
            else:
                continue
            liquidity = book.liquidity_snapshot(event.receive_wall_time)
            features.record_book(liquidity)
            try:
                snapshot = features.snapshot(now=event.receive_wall_time, liquidity=liquidity)
            except ValueError:
                continue
            evaluated: list[IntradayAlphaDecision] = []
            if "long" in models:
                evaluated.append(
                    long.evaluate(
                        market=market,
                        liquidity=liquidity,
                        features=snapshot,
                        context=GateContext(recent_gap=book.sequence_gap_count > 0),
                    )
                )
            if "short" in models:
                evaluated.append(
                    short.evaluate(
                        market=market,
                        liquidity=liquidity,
                        features=snapshot,
                        context=GateContext(recent_gap=book.sequence_gap_count > 0),
                    )
                )
            decision = (
                IntradayAlphaCoordinator.resolve(evaluated[0], evaluated[1])
                if len(evaluated) == 2
                else evaluated[0]
            )
            decisions.append(decision)
            liquidity_rows.append(liquidity)
            feature_rows.append(snapshot)
        statuses = Counter(item.status.value for item in decisions)
        reasons = Counter(
            item.no_trade_reason.value
            for item in decisions
            if item.no_trade_reason is not None
        )
        spreads = [item.spread_bps for item in liquidity_rows]
        imbalances = [item.depth_imbalance_20 for item in liquidity_rows]
        frequency = summarize_alpha_frequency(tuple(decisions))
        return {
            "book": {
                "status": book.status.value,
                "synchronized": book.synchronized,
                "last_update_id": book.update_id,
                "sequence_gap_count": book.sequence_gap_count,
                "resync_count": book.resync_count,
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
            },
            "liquidity": {
                "snapshot_count": len(liquidity_rows),
                "spread_bps_min": min(spreads) if spreads else None,
                "spread_bps_median": Decimal(str(median(spreads))) if spreads else None,
                "spread_bps_max": max(spreads) if spreads else None,
                "depth_imbalance_20_min": min(imbalances) if imbalances else None,
                "depth_imbalance_20_max": max(imbalances) if imbalances else None,
                "candle_volume_proxy_used": False,
            },
            "features": {
                "snapshot_count": len(feature_rows),
                "point_in_time": True,
                "future_events_used": False,
                "windows_ms": [250, 1000, 3000, 5000, 10000, 30000],
                "microprice_formula": "(ask*bid_qty + bid*ask_qty)/(bid_qty+ask_qty)",
                "ofi_formula": "Cont-style best bid/ask price-quantity contribution",
            },
            "alpha": {
                "evaluation_count": len(decisions),
                "long_count": statuses[AlphaDecisionStatus.LONG.value],
                "short_count": statuses[AlphaDecisionStatus.SHORT.value],
                "hold_count": statuses[AlphaDecisionStatus.HOLD.value],
                "no_trade_count": statuses[AlphaDecisionStatus.NO_TRADE.value],
                "calibration_status": CalibrationStatus.CALIBRATION_REQUIRED.value,
                "frequency_target": "5-20 CLOSED_TRADES_PER_ACTIVE_DAY_DIAGNOSTIC_ONLY",
                "thresholds_selected_by_pnl": False,
                "frequency": frequency,
            },
            "no_trade": {
                "total": statuses[AlphaDecisionStatus.NO_TRADE.value],
                "by_reason": dict(sorted(reasons.items())),
                "conflict_policy": "NO_TRADE_CONFLICT",
                "frequency_quota_enforced": False,
            },
        }

    @staticmethod
    def _elastic_contract() -> dict[str, object]:
        return {
            "profile": "ELASTIC_300_150_V0",
            "research_only": True,
            "baseline": "IMMEDIATE_PROFIT_EXIT",
            "selected": False,
            "continuation_grace_ms": 300,
            "reversal_confirmation_ms": 150,
            "hard_profit_floor_priority": True,
            "liquidity_failsafe": "LIQUIDITY_EXIT_FAILSAFE",
            "long_executable_reference": "SELL_VWAP_FROM_BIDS",
            "short_executable_reference": "BUY_VWAP_FROM_ASKS",
            "mark_price_executes_exit": False,
            "blocking_sleep_used": False,
            "validation": "SYNTHETIC_CONTRACT_TESTS_ONLY",
        }

    @staticmethod
    def _markdown(
        manifest: dict[str, object],
        diagnostics: dict[str, object],
        capture: dict[str, object],
    ) -> str:
        return f"""# Microstructure Foundation — Sprint 4A.1

Research-only intraday foundation for public Binance Spot and USD-M Futures data.
No authentication, account endpoint, Testnet, paper trading, external order or financial
candidate assessment is present.

## Capture

- Session: `{manifest['session']}`
- Events: `{capture.get('event_count', 0)}`
- Completeness: `{capture.get('completeness', 'UNKNOWN')}`
- Public-only payloads: yes

## Order book and replay

The local book uses buffered diff-depth updates, public REST snapshot alignment, explicit
sequence validation and fail-closed resynchronization. Replay uses a deterministic virtual
clock and never sleeps for strategic timers.

Book diagnostic: `{json.dumps(_jsonable(diagnostics['book']), sort_keys=True)}`

## Liquidity and features

Executable long exits consume bids; executable short exits consume asks. Mark price remains
restricted to Futures margin, maintenance and liquidation. Spread, microprice, top-5/10/20
depth, imbalance, aggressive flow, OFI, momentum, volatility and freshness are point-in-time.

## Alpha and NO_TRADE

Long and short are different classes, configurations, reason-code families and state. Numeric
thresholds remain `CALIBRATION_REQUIRED`; frequency is diagnostic and never a quota. Conflicting
long/short confirmations resolve to `NO_TRADE_CONFLICT`.

## Elastic Profit Exit

`ELASTIC_300_150_V0` is synthetic and unselected. A new executable-price peak resets 300 ms;
a persistent microstructure reversal requests exit after 150 ms. The hard profit floor and
liquidity failsafe have priority. No blocking sleep or executable mark-price assumption exists.

This foundation does not claim profitability and is not institutional HFT: Python scheduling,
public Internet latency, exchange aggregation and local clocks limit timing precision.
"""

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"capture manifest requires {key}")
    return value


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"unsupported microstructure report value: {type(value).__name__}")
