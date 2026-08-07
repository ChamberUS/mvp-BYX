"""Offline executable future labels, physically isolated from feature/alpha code."""

from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import io
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution.engine import ExecutionConfig, ExecutionSimulator
from adaptive_trader.execution.fees import FeeConfig, FeeModel
from adaptive_trader.execution.latency import LatencyProfile
from adaptive_trader.execution.models import (
    BookState,
    ExecutionPolicy,
    LiquidityRole,
    OrderSide,
    PositionEffect,
)
from adaptive_trader.microstructure.models import LiquiditySnapshot
from adaptive_trader.research.microstructure_edge_dataset import FeatureAnchor

ZERO = Decimal("0")
ONE = Decimal("1")
TEN_THOUSAND = Decimal("10000")
HORIZONS_MS = (250, 500, 1000, 3000, 5000, 15000, 30000, 60000)
NOTIONALS = (Decimal("100"), Decimal("500"), Decimal("1000"))
RESEARCH_FEE_PROFILE_ID = "RESEARCH_FEE_PROFILE_V1"


@dataclass(frozen=True, slots=True)
class ExecutableForwardLabel:
    anchor_id: str
    timestamp: str
    campaign: str
    session: str
    event_hashes: str
    replay_hash: str
    software_commit: str
    side: str
    horizon_ms: int
    requested_notional: Decimal
    executable_notional: Decimal
    filled_fraction: Decimal
    depth_fraction: Decimal
    entry_price: Decimal | None
    exit_price: Decimal | None
    gross_return_bps: Decimal | None
    entry_fee_bps: Decimal | None
    exit_fee_bps: Decimal | None
    spread_cost_bps: Decimal | None
    depth_slippage_bps: Decimal | None
    latency_slippage_bps: Decimal | None
    total_cost_bps: Decimal | None
    net_return_bps: Decimal | None
    executable: bool
    status: str
    mfe_bps_60s: Decimal | None
    mae_bps_60s: Decimal | None
    time_to_mfe_ms: int | None
    time_to_mae_ms: int | None
    fee_profile: str
    entry_taker_rate: Decimal
    exit_taker_rate: Decimal
    latency_profile: str
    mark_price_used_for_execution: bool = False


class ExecutableForwardLabeler:
    """Simulate independent long/short taker round trips on recorded public depth."""

    def __init__(
        self,
        *,
        notionals: tuple[Decimal, ...] = NOTIONALS,
        latency_profile: LatencyProfile = LatencyProfile.NORMAL,
        fee_config: FeeConfig | None = None,
        calculate_excursions: bool = True,
    ) -> None:
        if notionals != NOTIONALS:
            raise ValueError("notional tiers are pre-registered at 100,500,1000 USDT")
        if latency_profile is LatencyProfile.IDEALIZED:
            raise ValueError("IDEALIZED is diagnostic-only and not a baseline label profile")
        self.notionals = notionals
        self.latency_profile = latency_profile
        self.fee_config = fee_config or FeeConfig()
        self.fees = FeeModel(self.fee_config)
        self.calculate_excursions = calculate_excursions

    def label(
        self,
        anchors: tuple[FeatureAnchor, ...],
        states: tuple[LiquiditySnapshot, ...],
        side: PositionSide,
    ) -> tuple[ExecutableForwardLabel, ...]:
        if side is PositionSide.SHORT and any(
            anchor.market is not MarketType.USD_M_FUTURES for anchor in anchors
        ):
            raise ValueError("short forward labels are Futures-only")
        ordered = tuple(sorted(states, key=lambda item: item.timestamp))
        times = tuple(item.timestamp for item in ordered)
        rows: list[ExecutableForwardLabel] = []
        for anchor in anchors:
            for notional in self.notionals:
                quantity = notional / anchor.liquidity.mid_price
                entry = self._execute(
                    ordered, times, anchor.timestamp, quantity, side, opening=True
                )
                mfe = (
                    self._excursions(anchor.timestamp, side, entry, ordered, times)
                    if self.calculate_excursions
                    else (None, None, None, None)
                )
                for horizon_ms in HORIZONS_MS:
                    rows.append(
                        self._round_trip(
                            anchor, side, notional, horizon_ms, entry, ordered, times, mfe
                        )
                    )
        return tuple(rows)

    def _execute(
        self,
        states: tuple[LiquiditySnapshot, ...],
        times: tuple[datetime, ...],
        decision_time: datetime,
        quantity: Decimal,
        side: PositionSide,
        *,
        opening: bool,
        simulator: ExecutionSimulator | None = None,
    ) -> tuple[Decimal, Decimal | None, Decimal, Decimal, Decimal, Decimal] | None:
        simulator = simulator or self._simulator()
        arrival = simulator.latency.exchange_arrival(decision_time)
        index = bisect.bisect_left(times, arrival)
        if index >= len(states):
            return None
        state = states[index]
        if state.market_type is MarketType.SPOT and side is PositionSide.SHORT:
            return None
        book = BookState(
            timestamp=state.timestamp,
            market=state.market_type,
            symbol=state.symbol,
            bids=state.bids,
            asks=state.asks,
            synchronized=state.synchronized,
            sequence=index,
        )
        is_buy = (side is PositionSide.LONG) == opening
        effect = (
            PositionEffect.OPEN_LONG
            if side is PositionSide.LONG and opening
            else PositionEffect.CLOSE_LONG
            if side is PositionSide.LONG
            else PositionEffect.OPEN_SHORT
            if opening
            else PositionEffect.CLOSE_SHORT
        )
        reference = state.mid_price
        preview = simulator.preview_taker(
            book=book,
            side=OrderSide.BUY if is_buy else OrderSide.SELL,
            position_effect=effect,
            quantity=quantity,
            reference_price=reference,
        )
        if preview.filled_quantity <= ZERO or preview.vwap is None:
            return None
        decision_state_index = max(0, bisect.bisect_right(times, decision_time) - 1)
        decision_state = states[decision_state_index]
        decision_best = decision_state.best_ask if is_buy else decision_state.best_bid
        arrival_best = state.best_ask if is_buy else state.best_bid
        direction = ONE if is_buy else -ONE
        latency = direction * (arrival_best - decision_best) / reference * TEN_THOUSAND
        return (
            preview.filled_quantity,
            preview.vwap,
            preview.fee,
            preview.spread_crossing_bps,
            preview.depth_slippage_bps,
            latency,
        )

    def _round_trip(
        self,
        anchor: FeatureAnchor,
        side: PositionSide,
        requested_notional: Decimal,
        horizon_ms: int,
        entry: tuple[Decimal, Decimal | None, Decimal, Decimal, Decimal, Decimal] | None,
        states: tuple[LiquiditySnapshot, ...],
        times: tuple[datetime, ...],
        excursions: tuple[Decimal | None, Decimal | None, int | None, int | None],
    ) -> ExecutableForwardLabel:
        blank = self._base(anchor, side, requested_notional, horizon_ms, excursions)
        if entry is None or entry[1] is None:
            return ExecutableForwardLabel(**cast(Any, blank))
        entry_qty, entry_price, entry_fee, entry_spread, entry_depth, entry_latency = entry
        assert entry_price is not None
        exit_result = self._execute(
            states,
            times,
            anchor.timestamp + timedelta(milliseconds=horizon_ms),
            entry_qty,
            side,
            opening=False,
        )
        if exit_result is None or exit_result[1] is None:
            return ExecutableForwardLabel(**cast(Any, blank))
        exit_qty, exit_price, exit_fee, exit_spread, exit_depth, exit_latency = exit_result
        assert exit_price is not None
        common_qty = min(entry_qty, exit_qty)
        entry_notional = entry_price * common_qty
        gross = (
            (exit_price / entry_price - ONE) * TEN_THOUSAND
            if side is PositionSide.LONG
            else (entry_price / exit_price - ONE) * TEN_THOUSAND
        )
        entry_fee_bps = entry_fee / (entry_price * entry_qty) * TEN_THOUSAND
        exit_fee_bps = exit_fee / (exit_price * exit_qty) * TEN_THOUSAND
        fee_cost = entry_fee_bps + exit_fee_bps
        spread = entry_spread + exit_spread
        depth = entry_depth + exit_depth
        latency = entry_latency + exit_latency
        net_pnl = (
            (
                (exit_price - entry_price) * common_qty
                if side is PositionSide.LONG
                else (entry_price - exit_price) * common_qty
            )
            - entry_fee
            - exit_fee
        )
        net = net_pnl / entry_notional * TEN_THOUSAND
        fraction = common_qty / (requested_notional / anchor.liquidity.mid_price)
        executable = fraction == ONE
        return ExecutableForwardLabel(
            **cast(
                Any,
                {
                    **blank,
                    "executable_notional": entry_notional,
                    "filled_fraction": fraction,
                    "depth_fraction": entry_qty
                    / max(
                        ONE,
                        anchor.liquidity.visible_quantity(
                            PositionSide.LONG if side is PositionSide.LONG else PositionSide.SHORT
                        ),
                    ),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross_return_bps": gross,
                    "entry_fee_bps": entry_fee_bps,
                    "exit_fee_bps": exit_fee_bps,
                    "spread_cost_bps": spread,
                    "depth_slippage_bps": depth,
                    "latency_slippage_bps": latency,
                    "total_cost_bps": fee_cost + spread + depth + latency,
                    "net_return_bps": net,
                    "executable": executable,
                    "status": "NET_POSITIVE"
                    if net > ZERO
                    else "NET_ZERO"
                    if net == ZERO
                    else "NET_NEGATIVE",
                },
            )
        )

    def _simulator(self) -> ExecutionSimulator:
        return ExecutionSimulator(
            ExecutionConfig(
                policy=ExecutionPolicy.TAKER_ONLY,
                latency_profile=self.latency_profile,
                fee_config=self.fee_config,
                seed=42,
            )
        )

    def _base(
        self,
        anchor: FeatureAnchor,
        side: PositionSide,
        notional: Decimal,
        horizon_ms: int,
        excursions: tuple[Decimal | None, Decimal | None, int | None, int | None],
    ) -> dict[str, object]:
        rate = self.fees.rate(anchor.market, LiquidityRole.TAKER)
        return {
            "anchor_id": anchor.anchor_id,
            "timestamp": anchor.timestamp.isoformat(),
            "campaign": anchor.campaign_id,
            "session": anchor.session_id,
            "event_hashes": "|".join(anchor.event_hashes),
            "replay_hash": anchor.replay_hash,
            "software_commit": anchor.software_commit,
            "side": side.value,
            "horizon_ms": horizon_ms,
            "requested_notional": notional,
            "executable_notional": ZERO,
            "filled_fraction": ZERO,
            "depth_fraction": ZERO,
            "entry_price": None,
            "exit_price": None,
            "gross_return_bps": None,
            "entry_fee_bps": None,
            "exit_fee_bps": None,
            "spread_cost_bps": None,
            "depth_slippage_bps": None,
            "latency_slippage_bps": None,
            "total_cost_bps": None,
            "net_return_bps": None,
            "executable": False,
            "status": "NOT_EXECUTABLE",
            "mfe_bps_60s": excursions[0],
            "mae_bps_60s": excursions[1],
            "time_to_mfe_ms": excursions[2],
            "time_to_mae_ms": excursions[3],
            "fee_profile": RESEARCH_FEE_PROFILE_ID,
            "entry_taker_rate": rate,
            "exit_taker_rate": rate,
            "latency_profile": self.latency_profile.value,
            "mark_price_used_for_execution": False,
        }

    def _excursions(
        self,
        timestamp: datetime,
        side: PositionSide,
        entry: tuple[Decimal, Decimal | None, Decimal, Decimal, Decimal, Decimal] | None,
        states: tuple[LiquiditySnapshot, ...],
        times: tuple[datetime, ...],
    ) -> tuple[Decimal | None, Decimal | None, int | None, int | None]:
        if entry is None or entry[1] is None:
            return None, None, None, None
        quantity, entry_price, entry_fee, _, _, _ = entry
        assert entry_price is not None
        start = bisect.bisect_left(times, timestamp)
        end = bisect.bisect_right(times, timestamp + timedelta(seconds=60))
        outcomes: list[tuple[Decimal, int]] = []
        rate = self.fees.rate(MarketType.USD_M_FUTURES, LiquidityRole.TAKER)
        entry_fee_bps = entry_fee / (entry_price * quantity) * TEN_THOUSAND
        for state in states[start:end]:
            price = (
                state.executable_sell_price(quantity)
                if side is PositionSide.LONG
                else state.executable_buy_price(quantity)
            )
            if price is None:
                continue
            gross = (
                (price / entry_price - ONE) * TEN_THOUSAND
                if side is PositionSide.LONG
                else (entry_price / price - ONE) * TEN_THOUSAND
            )
            outcomes.append(
                (
                    gross - entry_fee_bps - rate * TEN_THOUSAND,
                    int((state.timestamp - timestamp).total_seconds() * 1000),
                )
            )
        if not outcomes:
            return None, None, None, None
        favorable = max(outcomes, key=lambda item: item[0])
        adverse = min(outcomes, key=lambda item: item[0])
        return favorable[0], adverse[0], favorable[1], adverse[1]


def write_labels(labels: tuple[ExecutableForwardLabel, ...], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not labels:
        path.write_text("anchor_id\n", encoding="utf-8")
    else:
        rows = [asdict(item) for item in labels]
        with path.open("wb") as raw:
            compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
            handle = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            handle.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_labels(path: Path) -> tuple[ExecutableForwardLabel, ...]:
    decimal_fields = {
        "requested_notional",
        "executable_notional",
        "filled_fraction",
        "depth_fraction",
        "entry_price",
        "exit_price",
        "gross_return_bps",
        "entry_fee_bps",
        "exit_fee_bps",
        "spread_cost_bps",
        "depth_slippage_bps",
        "latency_slippage_bps",
        "total_cost_bps",
        "net_return_bps",
        "mfe_bps_60s",
        "mae_bps_60s",
        "entry_taker_rate",
        "exit_taker_rate",
    }
    integer_fields = {"horizon_ms", "time_to_mfe_ms", "time_to_mae_ms"}
    optional_fields = {
        "entry_price",
        "exit_price",
        "gross_return_bps",
        "entry_fee_bps",
        "exit_fee_bps",
        "spread_cost_bps",
        "depth_slippage_bps",
        "latency_slippage_bps",
        "total_cost_bps",
        "net_return_bps",
        "mfe_bps_60s",
        "mae_bps_60s",
        "time_to_mfe_ms",
        "time_to_mae_ms",
    }
    rows: list[ExecutableForwardLabel] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            values: dict[str, object] = {}
            for name, value in raw.items():
                if name in optional_fields and value == "":
                    values[name] = None
                elif name in decimal_fields:
                    values[name] = Decimal(value)
                elif name in integer_fields:
                    values[name] = int(value)
                elif name in {"executable", "mark_price_used_for_execution"}:
                    values[name] = value == "True"
                else:
                    values[name] = value
            rows.append(ExecutableForwardLabel(**cast(Any, values)))
    return tuple(rows)
