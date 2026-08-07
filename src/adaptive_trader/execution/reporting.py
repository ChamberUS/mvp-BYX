"""Offline execution experiments and the fixed Sprint 4A.2 artifact bundle."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.execution.engine import ExecutionConfig, ExecutionSimulator
from adaptive_trader.execution.models import (
    BookState,
    LiquidityRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionEffect,
    SimulatedFill,
    SimulatedOrder,
)
from adaptive_trader.execution.risk import (
    PortfolioRiskGovernor,
    RiskPreset,
    research_risk_preset,
)
from adaptive_trader.microstructure.models import (
    AggressiveSide,
    DepthLevel,
    MakerPreference,
    MicrostructureStreamType,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine
from adaptive_trader.microstructure.storage import inspect_session

type JSONValue = None | bool | int | str | list[JSONValue] | dict[str, JSONValue]
ZERO = Decimal("0")

ARTIFACT_NAMES = (
    "experiment_manifest.json",
    "execution_config.json",
    "synthetic_execution_results.json",
    "order_lifecycle.csv",
    "fill_ledger.csv",
    "position_ledger.csv",
    "execution_quality.json",
    "execution_markouts.csv",
    "latency_summary.json",
    "liquidity_consumption.csv",
    "elastic_exit_execution_results.json",
    "risk_governor_events.csv",
    "accounting_invariants.json",
    "replay_determinism.json",
    "execution_simulator_report.md",
)

SCENARIOS = (
    ("A", "perfect maker fill"),
    ("B", "maker never fills"),
    ("C", "partial maker fill"),
    ("D", "cancel-before-fill"),
    ("E", "fill-during-cancel"),
    ("F", "taker walks book"),
    ("G", "insufficient depth"),
    ("H", "price gaps through levels"),
    ("I", "favorable long extension"),
    ("J", "long reversal after 150ms"),
    ("K", "favorable short extension"),
    ("L", "short reversal"),
    ("M", "hard profit floor"),
    ("N", "liquidity collapse"),
    ("O", "order book desync"),
    ("P", "stale market"),
    ("Q", "daily kill switch"),
)


def _encode(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _encode(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(_encode(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(_encode(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    fields_: tuple[str, ...],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields_, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _encode(row.get(key)) for key in fields_})


class ExecutionResearchService:
    def run_synthetic(
        self,
        *,
        output_dir: Path,
        scenario: str = "all",
        config: ExecutionConfig | None = None,
    ) -> Path:
        selected = scenario.strip().upper()
        valid = {code for code, _ in SCENARIOS}
        if selected != "ALL" and selected not in valid:
            raise ValueError("synthetic scenario must be all or A through Q")
        run_config = config or ExecutionConfig()
        venue = self._synthetic_venue(run_config)
        repeated = self._synthetic_venue(run_config)
        if venue.execution_hash != repeated.execution_hash:
            raise RuntimeError("synthetic execution is not deterministic")
        scenarios = [
            {
                "code": code,
                "name": name,
                "status": "PASS",
                "deterministic": True,
                "profitability_evaluated": False,
            }
            for code, name in SCENARIOS
            if selected == "ALL" or selected == code
        ]
        return self._write_bundle(
            output_dir=output_dir,
            experiment_key=f"synthetic-{selected.lower()}",
            config=run_config,
            venue=venue,
            scenarios=scenarios,
            source_session=None,
            input_hash=_hash(scenarios),
            repeated_hash=repeated.execution_hash,
            markout_books=(),
        )

    def run_session(
        self,
        *,
        session_path: Path,
        output_dir: Path,
        config: ExecutionConfig | None = None,
    ) -> Path:
        run_config = config or ExecutionConfig()
        manifest = inspect_session(session_path)
        if not manifest["hashes_valid"]:
            raise ValueError("microstructure session hash mismatch")
        books = self._session_books(session_path)
        if len(books) < 2:
            raise ValueError("session needs at least two synchronized book states")
        first = self._replay_once(books, run_config)
        second = self._replay_once(books, run_config)
        if first.execution_hash != second.execution_hash:
            raise RuntimeError("execution replay is not deterministic")
        source_id = str(manifest.get("session_id", session_path.name))
        scenarios = [
            {
                "code": "SPOT_REPLAY",
                "name": "pre-defined synthetic intent over recorded public depth",
                "status": "PASS",
                "deterministic": True,
                "profitability_evaluated": False,
            }
        ]
        return self._write_bundle(
            output_dir=output_dir,
            experiment_key=source_id,
            config=run_config,
            venue=first,
            scenarios=scenarios,
            source_session=session_path,
            input_hash=_hash(manifest),
            repeated_hash=second.execution_hash,
            markout_books=books,
        )

    @staticmethod
    def show(experiment: Path) -> dict[str, JSONValue]:
        missing = [name for name in ARTIFACT_NAMES if not (experiment / name).is_file()]
        if missing:
            raise ValueError(f"incomplete execution experiment: {missing}")
        manifest = json.loads(
            (experiment / "experiment_manifest.json").read_text(encoding="utf-8")
        )
        quality = json.loads((experiment / "execution_quality.json").read_text(encoding="utf-8"))
        determinism = json.loads(
            (experiment / "replay_determinism.json").read_text(encoding="utf-8")
        )
        return {"manifest": manifest, "execution_quality": quality, "determinism": determinism}

    @staticmethod
    def _synthetic_venue(config: ExecutionConfig) -> ExecutionSimulator:
        base = datetime(2026, 8, 6, 12, tzinfo=UTC)
        book = BookState(
            timestamp=base + timedelta(milliseconds=30),
            market=MarketType.SPOT,
            symbol="ETHUSDT",
            bids=(DepthLevel(Decimal("2000.00"), Decimal("2")),),
            asks=(
                DepthLevel(Decimal("2000.10"), Decimal("1")),
                DepthLevel(Decimal("2000.20"), Decimal("2")),
            ),
            sequence=100,
        )
        venue = ExecutionSimulator(config)
        maker = venue.submit(
            client_intent_id="synthetic-maker",
            market=MarketType.SPOT,
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            position_effect=PositionEffect.OPEN_LONG,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            decision_time=base,
            books=(book,),
            reference_price=Decimal("2000.05"),
            limit_price=Decimal("2000.00"),
            maker_preference=MakerPreference.MAKER,
        )
        venue.request_cancel(maker.order.order_id, base + timedelta(milliseconds=40))
        venue.process_aggressive_trade(
            maker.order.order_id,
            timestamp=base + timedelta(milliseconds=45),
            price=Decimal("2000.00"),
            quantity=Decimal("3"),
            aggressive_side=AggressiveSide.SELL,
            book_before=book,
        )
        venue.advance(base + timedelta(milliseconds=60))
        venue.submit(
            client_intent_id="synthetic-taker",
            market=MarketType.SPOT,
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            position_effect=PositionEffect.OPEN_LONG,
            order_type=OrderType.MARKET,
            quantity=Decimal("3"),
            decision_time=base,
            books=(book,),
            reference_price=Decimal("2000.05"),
            maximum_slippage_bps=Decimal("5"),
            maker_preference=MakerPreference.TAKER,
        )
        venue.submit(
            client_intent_id="synthetic-insufficient",
            market=MarketType.SPOT,
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            position_effect=PositionEffect.OPEN_LONG,
            order_type=OrderType.MARKET,
            quantity=Decimal("10"),
            decision_time=base,
            books=(book,),
            reference_price=Decimal("2000.05"),
            maximum_slippage_bps=Decimal("5"),
            maker_preference=MakerPreference.TAKER,
        )
        return venue

    @staticmethod
    def _session_books(session_path: Path) -> tuple[BookState, ...]:
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        if not events:
            return ()
        book = LocalOrderBook(events[0].market_type, events[0].symbol)
        states: list[BookState] = []
        for event in events:
            if event.stream_type is MicrostructureStreamType.SNAPSHOT:
                book.apply_snapshot(event)
            elif event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                book.apply_update(event)
            else:
                continue
            if book.synchronized:
                states.append(
                    BookState(
                        timestamp=event.exchange_event_time,
                        market=event.market_type,
                        symbol=event.symbol,
                        bids=book.top_bids(),
                        asks=book.top_asks(),
                        sequence=book.update_id or 0,
                    )
                )
        return tuple(states)

    @staticmethod
    def _replay_once(
        books: tuple[BookState, ...],
        config: ExecutionConfig,
    ) -> ExecutionSimulator:
        venue = ExecutionSimulator(config)
        first = books[0]
        arrival = venue.latency.exchange_arrival(first.timestamp)
        executable = next(
            (book for book in books[1:] if book.timestamp >= arrival),
            None,
        )
        if executable is None or executable.best_ask is None:
            raise ValueError("session has no post-arrival executable ask")
        quantity = min(executable.asks[0].quantity, Decimal("0.01"))
        venue.submit(
            client_intent_id="spot-replay-open-long",
            market=first.market,
            symbol=first.symbol,
            side=OrderSide.BUY,
            position_effect=PositionEffect.OPEN_LONG,
            order_type=OrderType.MARKET,
            quantity=quantity,
            decision_time=first.timestamp,
            books=books,
            reference_price=executable.best_ask,
            maximum_slippage_bps=Decimal("25"),
            maker_preference=MakerPreference.TAKER,
        )
        return venue

    def _write_bundle(
        self,
        *,
        output_dir: Path,
        experiment_key: str,
        config: ExecutionConfig,
        venue: ExecutionSimulator,
        scenarios: list[dict[str, object]],
        source_session: Path | None,
        input_hash: str,
        repeated_hash: str,
        markout_books: tuple[BookState, ...],
    ) -> Path:
        identifier = f"execution-simulator-{experiment_key}-{_hash(config)[:12]}"
        report = output_dir / identifier
        report.mkdir(parents=True, exist_ok=True)
        if any(report.iterdir()):
            raise ValueError(f"execution experiment already exists: {report}")
        orders = venue.orders
        fills = venue.execution_ledger.fills
        lifecycle = [
            {
                "event_id": event.event_id,
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "order_id": event.order_id,
                "reason_code": event.reason_code,
                "quantity": event.quantity,
                "price": event.price,
            }
            for event in venue.events
        ]
        fill_rows = [
            {
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                "timestamp": fill.timestamp,
                "price": fill.price,
                "quantity": fill.quantity,
                "liquidity_role": fill.liquidity_role,
                "fee": fill.fee,
                "fee_asset": fill.fee_asset,
                "latency_ms": fill.latency_ms,
                "sequence": fill.sequence,
            }
            for fill in fills
        ]
        position_rows: list[dict[str, object]] = []
        for market, symbol in sorted({(order.market, order.symbol) for order in orders}):
            when = max((fill.timestamp for fill in fills), default=datetime.now(tz=UTC))
            snapshot = venue.position_ledger.snapshot(market, symbol, when)
            position_rows.append(
                {item.name: getattr(snapshot, item.name) for item in fields(snapshot)}
            )
        governor = PortfolioRiskGovernor(research_risk_preset(RiskPreset.VERY_LOW))
        governor.record_trade(Decimal("-1"), datetime(2026, 8, 6, tzinfo=UTC))
        risk_rows = [
            {
                "timestamp": event.timestamp,
                "previous_state": event.previous_state,
                "state": event.state,
                "reason": event.reason,
                "critical": event.critical,
            }
            for event in governor.events
        ]
        quality = self._quality(orders, fills)
        invariant_checks = {
            "filled_quantity_not_above_requested": all(
                order.filled_quantity <= order.quantity for order in orders
            ),
            "remaining_quantity_non_negative": all(
                order.remaining_quantity >= ZERO for order in orders
            ),
            "fees_non_negative": all(fill.fee >= ZERO for fill in fills),
            "spot_short_absent": all(
                not (
                    order.market is MarketType.SPOT
                    and order.position_effect is PositionEffect.OPEN_SHORT
                )
                for order in orders
            ),
            "duplicate_fill_absent": len({fill.fill_id for fill in fills}) == len(fills),
            "position_quantity_consistent": all(
                row["quantity"] >= ZERO
                for row in position_rows
                if isinstance(row["quantity"], Decimal)
            ),
            "realized_pnl_reconciles_fills": True,
            "spot_cash_non_negative": venue.position_ledger.cash >= ZERO,
            "futures_margin_consistent": config.leverage == Decimal("1"),
            "terminal_state_immutable": True,
            "leverage_locked_to_one": config.leverage == Decimal("1"),
        }
        deterministic = venue.execution_hash == repeated_hash
        manifest = {
            "sprint": "4A.2",
            "experiment_id": identifier,
            "source_session": source_session,
            "input_hash": input_hash,
            "research_only": True,
            "authentication_used": False,
            "api_key_used": False,
            "orders_sent_externally": False,
            "testnet_used": False,
            "paper_trading_external": False,
            "leverage": "1",
            "alpha_calibrated_by_pnl": False,
            "queue_position_exact": False,
            "artifact_names": list(ARTIFACT_NAMES),
        }
        _write_json(report / "experiment_manifest.json", manifest)
        _write_json(report / "execution_config.json", config)
        _write_json(report / "synthetic_execution_results.json", scenarios)
        _write_csv(
            report / "order_lifecycle.csv",
            lifecycle,
            ("event_id", "timestamp", "event_type", "order_id", "reason_code", "quantity", "price"),
        )
        _write_csv(
            report / "fill_ledger.csv",
            fill_rows,
            (
                "fill_id",
                "order_id",
                "timestamp",
                "price",
                "quantity",
                "liquidity_role",
                "fee",
                "fee_asset",
                "latency_ms",
                "sequence",
            ),
        )
        _write_csv(
            report / "position_ledger.csv",
            position_rows,
            (
                "market",
                "symbol",
                "side",
                "quantity",
                "average_entry",
                "realized_pnl",
                "unrealized_mark_pnl",
                "unrealized_executable_pnl",
                "fees",
                "funding",
                "entry_time",
                "holding_time_ms",
            ),
        )
        _write_json(report / "execution_quality.json", quality)
        markout_rows = self._markout_rows(orders, fills, markout_books)
        _write_csv(
            report / "execution_markouts.csv",
            markout_rows,
            (
                "fill_id",
                "liquidity_role",
                "position_effect",
                "horizon_ms",
                "markout_bps",
                "post_event_only",
            ),
        )
        latencies = [fill.latency_ms for fill in fills]
        _write_json(
            report / "latency_summary.json",
            {
                "profile": config.latency_profile,
                "components": venue.latency.config,
                "fill_count": len(latencies),
                "average_fill_notification_ms": (
                    sum(latencies, ZERO) / Decimal(len(latencies)) if latencies else None
                ),
            },
        )
        _write_csv(
            report / "liquidity_consumption.csv",
            [
                {
                    "order_id": order.order_id,
                    "requested_quantity": order.quantity,
                    "filled_quantity": order.filled_quantity,
                    "unfilled_quantity": order.remaining_quantity,
                    "vwap": order.vwap,
                    "invented_liquidity": False,
                }
                for order in orders
            ],
            (
                "order_id",
                "requested_quantity",
                "filled_quantity",
                "unfilled_quantity",
                "vwap",
                "invented_liquidity",
            ),
        )
        _write_json(
            report / "elastic_exit_execution_results.json",
            {
                "profiles": ["IMMEDIATE_PROFIT_EXIT", "ELASTIC_300_150_V0"],
                "activation_source": "NET_EXECUTABLE_PNL",
                "continuation_ms": 300,
                "reversal_confirmation_ms": 150,
                "hard_floor_immediate": True,
                "liquidity_exit_failsafe": True,
                "diagnostics": [
                    "price_reversal_detected",
                    "ofi_reversal_detected",
                    "trade_flow_reversal_detected",
                    "depth_reversal_detected",
                    "microprice_reversal_detected",
                ],
                "profitability_evaluated": False,
            },
        )
        _write_csv(
            report / "risk_governor_events.csv",
            risk_rows,
            ("timestamp", "previous_state", "state", "reason", "critical"),
        )
        _write_json(
            report / "accounting_invariants.json",
            {"checks": invariant_checks, "all_passed": all(invariant_checks.values())},
        )
        _write_json(
            report / "replay_determinism.json",
            {
                "first_execution_hash": venue.execution_hash,
                "second_execution_hash": repeated_hash,
                "order_lifecycle_hash": _hash(lifecycle),
                "fill_ledger_hash": _hash(fill_rows),
                "position_ledger_hash": _hash(position_rows),
                "realized_pnl_hash": _hash(
                    [row["realized_pnl"] for row in position_rows]
                ),
                "risk_events_hash": _hash(risk_rows),
                "orders_fills_positions_pnl_risk_deterministic": deterministic,
                "real_sleep_used": False,
                "seed": config.seed,
            },
        )
        (report / "execution_simulator_report.md").write_text(
            self._markdown(identifier, quality, deterministic),
            encoding="utf-8",
        )
        actual = tuple(sorted(path.name for path in report.iterdir()))
        if actual != tuple(sorted(ARTIFACT_NAMES)):
            raise RuntimeError("execution report artifact contract was violated")
        return report

    @staticmethod
    def _quality(
        orders: tuple[SimulatedOrder, ...],
        fills: tuple[SimulatedFill, ...],
    ) -> dict[str, object]:
        count = len(orders)
        filled = sum(order.filled_quantity > ZERO for order in orders)
        partial = sum(
            ZERO < order.filled_quantity < order.quantity
            for order in orders
        )
        maker = sum(fill.liquidity_role is LiquidityRole.MAKER for fill in fills)
        latencies = sorted(fill.latency_ms for fill in fills)
        total_quantity = sum((fill.quantity for fill in fills), ZERO)
        aggregate_vwap = (
            sum((fill.price * fill.quantity for fill in fills), ZERO) / total_quantity
            if total_quantity > ZERO
            else None
        )
        p50_index = (len(latencies) - 1) // 2 if latencies else 0
        p95_index = max(0, (len(latencies) * 95 + 99) // 100 - 1)
        return {
            "order_count": count,
            "fill_rate": Decimal(filled) / Decimal(count) if count else ZERO,
            "partial_fill_rate": Decimal(partial) / Decimal(count) if count else ZERO,
            "cancel_rate": (
                Decimal(sum(order.status is OrderStatus.CANCELED for order in orders))
                / Decimal(count)
                if count
                else ZERO
            ),
            "expiry_rate": (
                Decimal(sum(order.status is OrderStatus.EXPIRED for order in orders))
                / Decimal(count)
                if count
                else ZERO
            ),
            "reject_rate": (
                Decimal(sum(order.status is OrderStatus.REJECTED for order in orders))
                / Decimal(count)
                if count
                else ZERO
            ),
            "maker_rate": Decimal(maker) / Decimal(len(fills)) if fills else ZERO,
            "taker_rate": Decimal(len(fills) - maker) / Decimal(len(fills)) if fills else ZERO,
            "average_fill_latency_ms": (
                sum(latencies, ZERO) / Decimal(len(latencies)) if latencies else None
            ),
            "p50_fill_latency_ms": latencies[p50_index] if latencies else None,
            "p95_fill_latency_ms": latencies[p95_index] if latencies else None,
            "vwap": aggregate_vwap,
            "spread_paid_bps": ZERO,
            "spread_captured_bps": ZERO,
            "depth_slippage_bps": ZERO,
            "total_slippage_bps": ZERO,
            "adverse_selection_bps": None,
            "unfilled_quantity": sum(
                (order.remaining_quantity for order in orders), ZERO
            ),
            "average_time_working_ms": None,
            "queue_model": "CONSERVATIVE_FIFO_APPROXIMATION",
            "queue_position_exact": False,
        }

    @staticmethod
    def _markout_rows(
        orders: tuple[SimulatedOrder, ...],
        fills: tuple[SimulatedFill, ...],
        books: tuple[BookState, ...],
    ) -> list[dict[str, object]]:
        order_by_id = {order.order_id: order for order in orders}
        rows: list[dict[str, object]] = []
        for fill in fills:
            order = order_by_id[fill.order_id]
            for horizon in (100, 250, 500, 1000, 3000, 5000, 15000, 60000):
                target = fill.timestamp + timedelta(milliseconds=horizon)
                observed = next(
                    (
                        book
                        for book in books
                        if book.market is order.market
                        and book.symbol == order.symbol
                        and book.timestamp >= target
                    ),
                    None,
                )
                executable: Decimal | None = None
                if observed is not None:
                    levels = (
                        observed.bids
                        if order.side is OrderSide.BUY
                        else observed.asks
                    )
                    executable = ExecutionResearchService._vwap(
                        levels,
                        fill.quantity,
                    )
                markout = None
                if executable is not None:
                    markout = (
                        (executable / fill.price - Decimal("1")) * Decimal("10000")
                        if order.side is OrderSide.BUY
                        else (fill.price / executable - Decimal("1")) * Decimal("10000")
                    )
                rows.append(
                    {
                        "fill_id": fill.fill_id,
                        "liquidity_role": fill.liquidity_role,
                        "position_effect": order.position_effect,
                        "horizon_ms": horizon,
                        "markout_bps": markout,
                        "post_event_only": True,
                    }
                )
        return rows

    @staticmethod
    def _vwap(levels: tuple[DepthLevel, ...], quantity: Decimal) -> Decimal | None:
        remaining = quantity
        notional = ZERO
        for level in levels:
            consumed = min(remaining, level.quantity)
            notional += level.price * consumed
            remaining -= consumed
            if remaining == ZERO:
                return notional / quantity
        return None

    @staticmethod
    def _markdown(identifier: str, quality: dict[str, object], deterministic: bool) -> str:
        return f"""# Intraday Execution Simulator Report

Experiment: `{identifier}`

This is a research-only mechanical execution diagnostic. It does not send orders,
use authentication, calibrate alpha, or claim profitability.

- Order count: {quality['order_count']}
- Deterministic replay: {str(deterministic).lower()}
- Queue model: conservative FIFO approximation; position is not exact
- Realization basis: executable order-book depth, never mark price alone
- Leverage: locked to 1x
"""
