"""Pure audit and frequency-selection rules for pullback calibration."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.research.pullback_analysis import PullbackFold, PullbackRun
from adaptive_trader.strategy.pullback import PullbackDecisionTrace

HUNDRED = Decimal("100")

RULE_ORDER = (
    "regime_matched",
    "ema_alignment",
    "price_long_ema_side",
    "trend_persistence",
    "pullback_started",
    "pullback_age_valid",
    "pullback_depth_min",
    "pullback_depth_max",
    "long_ema_not_crossed",
    "resumption_cross",
    "directional_close_confirmation",
    "entry_extension_valid",
    "volume_valid",
    "volatility_valid",
)

ABLATION_RULES = (
    "volume_valid",
    "volatility_valid",
    "entry_extension_valid",
    "directional_close_confirmation",
    "regime_matched",
    "pullback_depth_min",
    "trend_persistence",
)

FAILURE_CODES = {
    "regime_matched": "REGIME_AFTER_PULLBACK_REJECTED",
    "ema_alignment": "EMA_ALIGNMENT_REJECTED",
    "price_long_ema_side": "PRICE_CROSSED_LONG_EMA",
    "trend_persistence": "TREND_PERSISTENCE_TOO_SHORT",
    "pullback_started": "NO_PULLBACK",
    "pullback_age_valid": "PULLBACK_TOO_OLD",
    "pullback_depth_min": "PULLBACK_TOO_SHALLOW",
    "pullback_depth_max": "PULLBACK_TOO_DEEP",
    "long_ema_not_crossed": "PRICE_CROSSED_LONG_EMA",
    "resumption_cross": "RESUMPTION_NOT_CONFIRMED",
    "directional_close_confirmation": "DIRECTIONAL_CLOSE_REJECTED",
    "entry_extension_valid": "PRICE_OVEREXTENDED",
    "volume_valid": "VOLUME_REJECTED",
    "volatility_valid": "VOLATILITY_REJECTED",
}


class OperationalStatus(StrEnum):
    OPERATIONALLY_VIABLE = "OPERATIONALLY_VIABLE"
    TOO_RESTRICTIVE = "TOO_RESTRICTIVE"
    TOO_PERMISSIVE = "TOO_PERMISSIVE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class OperationalFrequency:
    market: str
    mode: str
    variant_id: str
    pullbacks: int
    resumptions: int
    signals: int
    trades: int
    trades_per_year: Decimal
    fold_count: int
    folds_with_trades: int
    folds_with_trades_percent: Decimal
    zero_trade_fold_percent: Decimal
    long_signals: int
    short_signals: int
    exposure_percent: Decimal
    status: OperationalStatus


def trace_checks(trace: PullbackDecisionTrace) -> dict[str, bool]:
    return {
        "regime_matched": trace.regime_matched,
        "ema_alignment": trace.ema_alignment,
        "price_long_ema_side": trace.price_long_ema_side,
        "trend_persistence": trace.persistence_valid,
        "pullback_started": trace.pullback_detected,
        "pullback_age_valid": trace.pullback_age_valid,
        "pullback_depth_min": trace.pullback_depth_min_valid,
        "pullback_depth_max": trace.pullback_depth_max_valid,
        "long_ema_not_crossed": trace.long_ema_not_crossed,
        "resumption_cross": trace.resumption_cross,
        "directional_close_confirmation": (
            trace.directional_close_confirmation
        ),
        "entry_extension_valid": trace.entry_extension_valid,
        "volume_valid": trace.volume_valid,
        "volatility_valid": trace.volatility_valid,
    }


def all_failure_codes(trace: PullbackDecisionTrace) -> tuple[str, ...]:
    checks = trace_checks(trace)
    return tuple(
        FAILURE_CODES[rule] for rule in RULE_ORDER if not checks[rule]
    )


def first_failure_code(trace: PullbackDecisionTrace) -> str | None:
    failures = all_failure_codes(trace)
    return failures[0] if failures else None


def logic_audit_rows(
    traces: tuple[PullbackDecisionTrace, ...],
) -> tuple[dict[str, object], ...]:
    metadata = {
        "regime_matched": (
            (),
            (),
            ("trend_persistence",),
            "Established regime is locked at pullback start; it is not "
            "re-required on the resumption candle.",
        ),
        "ema_alignment": (("regime_matched",), (), (), "Short/long EMA order."),
        "price_long_ema_side": (
            ("ema_alignment",),
            (),
            ("long_ema_not_crossed",),
            "Directional close must remain on the trend side of long EMA.",
        ),
        "trend_persistence": (
            ("price_long_ema_side",),
            (),
            ("regime_matched",),
            "Consecutive established-trend candles before pullback.",
        ),
        "pullback_started": (
            ("trend_persistence",),
            (),
            (),
            "Close reaches the pullback side of short EMA.",
        ),
        "pullback_age_valid": (
            ("pullback_started",),
            (),
            (),
            "State age lies inside the pre-registered candle bounds.",
        ),
        "pullback_depth_min": (
            ("pullback_started",),
            (),
            (),
            "Maximum observed depth reaches the configured minimum.",
        ),
        "pullback_depth_max": (
            ("pullback_depth_min",),
            (),
            (),
            "Maximum observed depth does not exceed the configured maximum.",
        ),
        "long_ema_not_crossed": (
            ("pullback_started",),
            (),
            ("price_long_ema_side",),
            "Mirrored directional long-EMA boundary.",
        ),
        "resumption_cross": (
            ("long_ema_not_crossed",),
            (),
            (),
            "Close returns through the short EMA in trend direction.",
        ),
        "directional_close_confirmation": (
            ("resumption_cross",),
            (),
            (),
            "Close advances beyond the immediately previous close.",
        ),
        "entry_extension_valid": (
            ("resumption_cross",),
            (),
            (),
            "Directional distance from long EMA in ATR units.",
        ),
        "volume_valid": (
            ("entry_extension_valid",),
            (),
            (),
            "Current volume ratio reaches the configured minimum.",
        ),
        "volatility_valid": (
            ("volume_valid",),
            (),
            (),
            "ATR divided by close does not exceed the configured maximum.",
        ),
    }
    rows: list[dict[str, object]] = []
    for order, rule in enumerate(RULE_ORDER, start=1):
        depends, exclusive, redundant, notes = metadata[rule]
        passed = sum(trace_checks(trace)[rule] for trace in traces)
        rows.append(
            {
                "rule_id": rule,
                "order": order,
                "required": True,
                "depends_on": depends,
                "mutually_exclusive_with": exclusive,
                "redundant_with": redundant,
                "observed_pass_count": passed,
                "observed_fail_count": len(traces) - passed,
                "notes": notes,
            }
        )
    return tuple(rows)


def rejected_resumption_rows(
    run: PullbackRun,
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for trace in run.pullback_traces:
        if not trace.resumption_cross or trace.signal_created:
            continue
        checks = trace_checks(trace)
        failures = all_failure_codes(trace)
        rows.append(
            {
                "timestamp": trace.timestamp,
                "market": run.market,
                "mode": run.mode,
                "side": (
                    trace.side.value
                    if trace.side is not None
                    else _inferred_side(trace)
                ),
                "regime": trace.regime.value,
                "short_ema": trace.short_ema,
                "long_ema": trace.long_ema,
                "close": trace.close_price,
                "previous_close": trace.previous_close,
                "atr": trace.atr,
                "atr_relative": trace.atr_relative,
                "volume_ratio": trace.volume_ratio,
                "pullback_age": trace.pullback_age,
                "pullback_depth_atr": trace.pullback_depth_atr,
                "entry_extension_atr": trace.entry_extension_atr,
                "trend_persistence": trace.trend_persistence_count,
                **checks,
                "first_failure_code": failures[0] if failures else None,
                "all_failure_codes": ";".join(failures),
                "hypothetical_signal_without_failed_rule": (
                    len(failures) == 1
                ),
            }
        )
    return tuple(rows)


def _inferred_side(trace: PullbackDecisionTrace) -> str:
    return "LONG" if trace.short_ema > trace.long_ema else "SHORT"


def single_rule_ablation_rows(
    run: PullbackRun,
) -> tuple[dict[str, object], ...]:
    rejected = tuple(
        trace
        for trace in run.pullback_traces
        if trace.resumption_cross and not trace.signal_created
    )
    rows: list[dict[str, object]] = []
    for removed_rule in ABLATION_RULES:
        eligible = 0
        for trace in rejected:
            checks = trace_checks(trace)
            if all(
                passed or rule == removed_rule
                for rule, passed in checks.items()
            ):
                eligible += 1
        rows.append(
            {
                "market": run.market,
                "mode": run.mode,
                "variant_id": run.variant_id,
                "period": run.period,
                "removed_rule": removed_rule,
                "rules_removed_count": 1,
                "rejected_resumptions": len(rejected),
                "hypothetically_eligible": eligible,
                "signals_executed": 0,
                "diagnostic_only": True,
            }
        )
    return tuple(rows)


def sequential_funnel_rows(
    run: PullbackRun,
) -> tuple[dict[str, object], ...]:
    traces = run.pullback_traces
    stages: list[tuple[str, int, int, str | None]] = []
    previous_passed = run.evaluated_candles
    stages.append(
        ("evaluated_candles", run.evaluated_candles, run.evaluated_candles, None)
    )
    for rule in RULE_ORDER:
        entered = previous_passed
        observed = sum(trace_checks(trace)[rule] for trace in traces)
        passed = min(entered, observed)
        failed_codes = Counter(
            FAILURE_CODES[rule]
            for trace in traces
            if not trace_checks(trace)[rule]
        )
        dominant = (
            failed_codes.most_common(1)[0][0] if failed_codes else None
        )
        stages.append((rule, entered, passed, dominant))
        previous_passed = passed
    terminal = (
        ("signal_created", run.long_signals + run.short_signals),
        ("risk_approved", run.approvals),
        ("order_executed", run.executions),
        ("trade_closed", len(run.trades)),
    )
    for stage, observed in terminal:
        entered = previous_passed
        passed = min(entered, observed)
        stages.append((stage, entered, passed, None))
        previous_passed = passed
    rows: list[dict[str, object]] = []
    evaluated = run.evaluated_candles
    for stage, entered, passed, dominant in stages:
        failed = entered - passed
        rows.append(
            {
                "market": run.market,
                "mode": run.mode,
                "variant_id": run.variant_id,
                "period": run.period,
                "stage": stage,
                "entered_count": entered,
                "passed_count": passed,
                "failed_count": failed,
                "pass_percent_from_previous": (
                    Decimal(passed) / Decimal(entered) * HUNDRED
                    if entered
                    else Decimal("0")
                ),
                "pass_percent_from_evaluated": (
                    Decimal(passed) / Decimal(evaluated) * HUNDRED
                    if evaluated
                    else Decimal("0")
                ),
                "dominant_failure_reason": dominant,
            }
        )
    return tuple(rows)


def operational_frequency(
    run: PullbackRun,
    folds: tuple[PullbackFold, ...],
    *,
    baseline_directional_trades: int,
    exposure_percent: Decimal = Decimal("0"),
) -> OperationalFrequency:
    signals = run.long_signals + run.short_signals
    trades = len(run.trades)
    folds_with_trades = sum(bool(fold.run.trades) for fold in folds)
    fold_count = len(folds)
    folds_percent = (
        Decimal(folds_with_trades) / Decimal(fold_count) * HUNDRED
        if fold_count
        else Decimal("0")
    )
    zero_percent = HUNDRED - folds_percent if fold_count else HUNDRED
    too_permissive = (
        signals > baseline_directional_trades * 5
        or exposure_percent > Decimal("50")
    )
    sufficient = (
        signals >= 12
        and trades >= 10
        and folds_percent >= Decimal("50")
        and zero_percent <= Decimal("50")
    )
    if too_permissive:
        status = OperationalStatus.TOO_PERMISSIVE
    elif sufficient:
        status = OperationalStatus.OPERATIONALLY_VIABLE
    elif signals < 12 or trades < 10:
        status = OperationalStatus.TOO_RESTRICTIVE
    else:
        status = OperationalStatus.INCONCLUSIVE
    return OperationalFrequency(
        market=run.market,
        mode=run.mode,
        variant_id=run.variant_id,
        pullbacks=run.pullbacks_detected,
        resumptions=run.resumptions,
        signals=signals,
        trades=trades,
        trades_per_year=Decimal(trades) / Decimal("2"),
        fold_count=fold_count,
        folds_with_trades=folds_with_trades,
        folds_with_trades_percent=folds_percent,
        zero_trade_fold_percent=zero_percent,
        long_signals=run.long_signals,
        short_signals=run.short_signals,
        exposure_percent=exposure_percent,
        status=status,
    )


def select_by_frequency(
    frequencies: tuple[OperationalFrequency, ...],
    *,
    catalog_order: tuple[str, ...],
    complexity: dict[str, int],
    maximum: int = 2,
) -> tuple[str, ...]:
    viable = [
        item
        for item in frequencies
        if item.status is OperationalStatus.OPERATIONALLY_VIABLE
    ]

    def target_distance(trades: int) -> int:
        if 10 <= trades <= 60:
            return 0
        return min(abs(trades - 10), abs(trades - 60))

    order = {variant_id: index for index, variant_id in enumerate(catalog_order)}
    viable.sort(
        key=lambda item: (
            -item.folds_with_trades,
            item.zero_trade_fold_percent,
            target_distance(item.trades),
            complexity[item.variant_id],
            order[item.variant_id],
        )
    )
    return tuple(item.variant_id for item in viable[:maximum])
