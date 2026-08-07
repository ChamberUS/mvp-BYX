"""Deterministic descriptive statistics for executable microstructure labels."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from adaptive_trader.microstructure.campaign import DatasetSufficiency
from adaptive_trader.research.executable_forward_labels import ExecutableForwardLabel
from adaptive_trader.research.microstructure_edge_dataset import FeatureAnchor, feature_anchor_row

PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)
FEATURES = (
    "spread_bps",
    "depth_5_bid",
    "depth_5_ask",
    "depth_10_bid",
    "depth_10_ask",
    "depth_20_bid",
    "depth_20_ask",
    "microprice_edge_bps",
    "imbalance_5",
    "imbalance_10",
    "imbalance_20",
    "ofi_250ms",
    "ofi_1s",
    "ofi_3s",
    "aggressive_flow_250ms",
    "aggressive_flow_1s",
    "aggressive_flow_3s",
    "aggressive_flow_10s",
    "momentum_250ms_bps",
    "momentum_1s_bps",
    "momentum_3s_bps",
    "momentum_10s_bps",
    "volatility_1s_bps",
    "volatility_5s_bps",
    "volatility_30s_bps",
    "book_age_ms",
    "event_age_ms",
)
PAIRWISE = (
    ("ofi_1s", "aggressive_flow_1s"),
    ("imbalance_10", "microprice_edge_bps"),
    ("ofi_3s", "momentum_3s_bps"),
    ("spread_bps", "depth_10_bid"),
    ("volatility_5s_bps", "ofi_1s"),
)


class EdgeCharacterizer:
    def __init__(
        self,
        anchors: tuple[FeatureAnchor, ...],
        long_labels: tuple[ExecutableForwardLabel, ...],
        short_labels: tuple[ExecutableForwardLabel, ...],
        status: DatasetSufficiency,
        *,
        cut_point_anchors: tuple[FeatureAnchor, ...] | None = None,
    ) -> None:
        self.anchors = anchors
        self.anchor_rows = {item.anchor_id: feature_anchor_row(item) for item in anchors}
        self.cut_point_rows = {
            item.anchor_id: feature_anchor_row(item)
            for item in (cut_point_anchors if cut_point_anchors is not None else anchors)
        }
        self.long_labels = long_labels
        self.short_labels = short_labels
        self.status = status

    def write(self, output: Path) -> dict[str, object]:
        output.mkdir(parents=True, exist_ok=True)
        distributions, bounds = self._distributions()
        _json(output / "feature_distribution.json", distributions)
        _csv(output / "long_univariate_edge.csv", self._univariate(self.long_labels, bounds))
        _csv(output / "short_univariate_edge.csv", self._univariate(self.short_labels, bounds))
        coarse: dict[str, tuple[float, float]] = {
            name: (_required_quantile(values, 1 / 3), _required_quantile(values, 2 / 3))
            for name, values in self._feature_values(cut_points=True).items()
        }
        _csv(output / "long_pairwise_edge.csv", self._pairwise(self.long_labels, coarse))
        _csv(output / "short_pairwise_edge.csv", self._pairwise(self.short_labels, coarse))
        _csv(output / "time_of_day_edge.csv", self._time_of_day())
        liquidity, volatility = self._regimes(coarse)
        _csv(output / "liquidity_regime_edge.csv", liquidity)
        _csv(output / "volatility_regime_edge.csv", volatility)
        bootstrap = self._bootstrap()
        _json(output / "block_bootstrap.json", bootstrap)
        _csv(output / "temporal_stability.csv", self._temporal_stability())
        _csv(output / "no_trade_context_analysis.csv", self._no_trade(coarse))
        comparison = self._comparison()
        _json(output / "long_short_comparison.json", comparison)
        return {"feature_bounds": bounds, "bootstrap": bootstrap, "comparison": comparison}

    def _feature_values(self, *, cut_points: bool = False) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        rows = self.cut_point_rows if cut_points else self.anchor_rows
        for name in FEATURES:
            values = [_number(row.get(name)) for row in rows.values()]
            result[name] = [item for item in values if item is not None]
        return result

    def _distributions(self) -> tuple[dict[str, object], dict[str, tuple[float, ...]]]:
        feature_output: dict[str, object] = {}
        output: dict[str, object] = {
            "partition_used_for_cut_points": (
                "DISCOVERY"
                if self.status
                in {DatasetSufficiency.DISCOVERY_READY, DatasetSufficiency.CONFIRMATION_READY}
                else "ENGINEERING_SAMPLE"
            ),
            "confirmation_recalculated": False,
            "features": feature_output,
        }
        bounds: dict[str, tuple[float, ...]] = {}
        values_by_feature = self._feature_values(cut_points=True)
        for name, values in values_by_feature.items():
            cuts = (
                tuple(_required_quantile(values, item / 5) for item in range(1, 5))
                if values
                else ()
            )
            bounds[name] = cuts
            feature_output[name] = {
                "sample_count": len(values),
                "missing_count": len(self.cut_point_rows) - len(values),
                "mean": statistics.fmean(values) if values else None,
                "percentiles": {f"p{p}": _quantile(values, p / 100) for p in PERCENTILES},
                "quantile_bin_cut_points": cuts,
            }
        return output, bounds

    def _univariate(
        self, labels: tuple[ExecutableForwardLabel, ...], bounds: dict[str, tuple[float, ...]]
    ) -> list[dict[str, object]]:
        groups: dict[tuple[object, ...], list[ExecutableForwardLabel]] = defaultdict(list)
        for label in labels:
            anchor = self.anchor_rows[label.anchor_id]
            for feature in FEATURES:
                value = _number(anchor.get(feature))
                if value is None:
                    continue
                groups[
                    (
                        feature,
                        _bin(value, bounds[feature]),
                        label.horizon_ms,
                        str(label.requested_notional),
                    )
                ].append(label)
        return [
            {
                "partition": self._partition_name(),
                "feature": key[0],
                "bin": key[1],
                "horizon_ms": key[2],
                "notional": key[3],
                **_label_metrics(items),
            }
            for key, items in sorted(groups.items(), key=lambda item: str(item[0]))
        ]

    def _pairwise(
        self, labels: tuple[ExecutableForwardLabel, ...], bounds: dict[str, tuple[float, float]]
    ) -> list[dict[str, object]]:
        groups: dict[tuple[object, ...], list[ExecutableForwardLabel]] = defaultdict(list)
        for label in labels:
            anchor = self.anchor_rows[label.anchor_id]
            for left, right in PAIRWISE:
                lv, rv = _number(anchor[left]), _number(anchor[right])
                if lv is None or rv is None:
                    continue
                groups[
                    (
                        left,
                        right,
                        _coarse(lv, bounds[left]),
                        _coarse(rv, bounds[right]),
                        label.horizon_ms,
                        str(label.requested_notional),
                    )
                ].append(label)
        return [
            {
                "partition": self._partition_name(),
                "feature_left": key[0],
                "feature_right": key[1],
                "left_bin": key[2],
                "right_bin": key[3],
                "horizon_ms": key[4],
                "notional": key[5],
                **_label_metrics(items),
            }
            for key, items in sorted(groups.items(), key=lambda item: str(item[0]))
        ]

    def _time_of_day(self) -> list[dict[str, object]]:
        groups: dict[tuple[object, ...], list[ExecutableForwardLabel]] = defaultdict(list)
        for side, labels in (("LONG", self.long_labels), ("SHORT", self.short_labels)):
            for label in labels:
                hour = datetime.fromisoformat(label.timestamp).hour
                groups[(side, hour, label.horizon_ms, str(label.requested_notional))].append(label)
        rows: list[dict[str, object]] = []
        for key, items in sorted(groups.items()):
            spreads = [
                _number(self.anchor_rows[item.anchor_id]["spread_bps"]) or 0 for item in items
            ]
            vol = [
                _number(self.anchor_rows[item.anchor_id]["volatility_5s_bps"]) or 0
                for item in items
            ]
            rows.append(
                {
                    "side": key[0],
                    "utc_hour": key[1],
                    "horizon_ms": key[2],
                    "notional": key[3],
                    "average_spread_bps": statistics.fmean(spreads),
                    "average_volatility_5s_bps": statistics.fmean(vol),
                    **_label_metrics(items),
                }
            )
        return rows

    def _regimes(
        self, coarse: dict[str, tuple[float, float]]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        liquidity_groups: dict[tuple[object, ...], list[ExecutableForwardLabel]] = defaultdict(list)
        volatility_groups: dict[tuple[object, ...], list[ExecutableForwardLabel]] = defaultdict(
            list
        )
        spread_cut = coarse["spread_bps"][1]
        depth_values = [
            (_number(row["depth_10_bid"]) or 0) + (_number(row["depth_10_ask"]) or 0)
            for row in self.cut_point_rows.values()
        ]
        depth_cut = _required_quantile(depth_values, 0.5)
        vol_values = [
            (_number(row["volatility_5s_bps"]) or 0) for row in self.cut_point_rows.values()
        ]
        vol_cuts = tuple(_required_quantile(vol_values, q) for q in (0.25, 0.5, 0.75))
        for side, labels in (("LONG", self.long_labels), ("SHORT", self.short_labels)):
            for label in labels:
                row = self.anchor_rows[label.anchor_id]
                spread = _number(row["spread_bps"]) or 0
                depth = (_number(row["depth_10_bid"]) or 0) + (_number(row["depth_10_ask"]) or 0)
                spread_name = "TIGHT" if spread <= spread_cut else "WIDE"
                depth_name = "DEEP" if depth >= depth_cut else "THIN"
                liquid = f"{spread_name}_{depth_name}"
                vol = _number(row["volatility_5s_bps"]) or 0
                vol_regime = (
                    "LOW"
                    if vol <= vol_cuts[0]
                    else "MEDIUM"
                    if vol <= vol_cuts[1]
                    else "HIGH"
                    if vol <= vol_cuts[2]
                    else "EXTREME"
                )
                base = (side, label.horizon_ms, str(label.requested_notional))
                liquidity_groups[(*base, liquid)].append(label)
                volatility_groups[(*base, vol_regime)].append(label)
        liquidity_rows = [
            {
                "side": key[0],
                "horizon_ms": key[1],
                "notional": key[2],
                "regime": key[3],
                **_label_metrics(items),
            }
            for key, items in sorted(liquidity_groups.items())
        ]
        volatility_rows = [
            {
                "side": key[0],
                "horizon_ms": key[1],
                "notional": key[2],
                "regime": key[3],
                **_label_metrics(items),
            }
            for key, items in sorted(volatility_groups.items())
        ]
        return liquidity_rows, volatility_rows

    def _bootstrap(self) -> dict[str, object]:
        result_groups: list[dict[str, object]] = []
        result: dict[str, object] = {
            "method": "DETERMINISTIC_TEMPORAL_BLOCK_BOOTSTRAP",
            "block_size_seconds": 900,
            "iterations": 2000,
            "seed": 42,
            "iid_assumption": False,
            "groups": result_groups,
        }
        for side, labels in (("LONG", self.long_labels), ("SHORT", self.short_labels)):
            grouped: dict[tuple[int, str], list[ExecutableForwardLabel]] = defaultdict(list)
            for item in labels:
                if item.net_return_bps is not None:
                    grouped[(item.horizon_ms, str(item.requested_notional))].append(item)
            for (horizon, notional), items in sorted(grouped.items()):
                blocks: dict[int, list[float]] = defaultdict(list)
                for item in items:
                    block = int(datetime.fromisoformat(item.timestamp).timestamp()) // 900
                    if item.net_return_bps is not None:
                        blocks[block].append(float(item.net_return_bps))
                result_groups.append(
                    {
                        "side": side,
                        "horizon_ms": horizon,
                        "notional": notional,
                        **_block_bootstrap(tuple(blocks.values()), iterations=2000, seed=42),
                    }
                )
        return result

    def _temporal_stability(self) -> list[dict[str, object]]:
        groups: dict[tuple[object, ...], list[ExecutableForwardLabel]] = defaultdict(list)
        for side, labels in (("LONG", self.long_labels), ("SHORT", self.short_labels)):
            for item in labels:
                hour = datetime.fromisoformat(item.timestamp).strftime("%Y-%m-%dT%H:00Z")
                groups[(side, hour, item.horizon_ms, str(item.requested_notional))].append(item)
        return [
            {
                "side": key[0],
                "temporal_block": key[1],
                "horizon_ms": key[2],
                "notional": key[3],
                **_label_metrics(items),
            }
            for key, items in sorted(groups.items())
        ]

    def _no_trade(self, coarse: dict[str, tuple[float, float]]) -> list[dict[str, object]]:
        combined: dict[tuple[int, str, str], dict[str, list[ExecutableForwardLabel]]] = defaultdict(
            lambda: {"LONG": [], "SHORT": []}
        )
        for side, labels in (("LONG", self.long_labels), ("SHORT", self.short_labels)):
            for item in labels:
                row = self.anchor_rows[item.anchor_id]
                spread_bin = _coarse(_number(row["spread_bps"]) or 0, coarse["spread_bps"])
                volatility_bin = _coarse(
                    _number(row["volatility_5s_bps"]) or 0,
                    coarse["volatility_5s_bps"],
                )
                context = f"SPREAD_{spread_bin}|VOL_{volatility_bin}"
                combined[(item.horizon_ms, str(item.requested_notional), context)][side].append(
                    item
                )
        rows: list[dict[str, object]] = []
        for key, sides in sorted(combined.items()):
            long_net = _net_values(sides["LONG"])
            short_net = _net_values(sides["SHORT"])
            rows.append(
                {
                    "horizon_ms": key[0],
                    "notional": key[1],
                    "context": key[2],
                    "long_samples": len(long_net),
                    "short_samples": len(short_net),
                    "long_mean_net_bps": statistics.fmean(long_net) if long_net else None,
                    "short_mean_net_bps": statistics.fmean(short_net) if short_net else None,
                    "both_weak_or_negative": bool(
                        long_net
                        and short_net
                        and statistics.fmean(long_net) <= 0
                        and statistics.fmean(short_net) <= 0
                    ),
                    "strategy_threshold_created": False,
                }
            )
        return rows

    def _comparison(self) -> dict[str, object]:
        long_metrics = _label_metrics(list(self.long_labels))
        short_metrics = _label_metrics(list(self.short_labels))
        return {
            "long": long_metrics,
            "short": short_metrics,
            "pnl_summed_across_sides": False,
            "interpretation": "BOTH_INCONCLUSIVE"
            if self.status is DatasetSufficiency.ENGINEERING_ONLY
            else "DESCRIPTIVE_ONLY",
        }

    def _partition_name(self) -> str:
        return (
            "DISCOVERY"
            if self.status
            in {DatasetSufficiency.DISCOVERY_READY, DatasetSufficiency.CONFIRMATION_READY}
            else "ENGINEERING_SAMPLE"
        )


def _label_metrics(items: list[ExecutableForwardLabel]) -> dict[str, object]:
    net = _net_values(items)
    costs = [float(item.total_cost_bps) for item in items if item.total_cost_bps is not None]
    slip = [float(item.depth_slippage_bps) for item in items if item.depth_slippage_bps is not None]
    mfe = [float(item.mfe_bps_60s) for item in items if item.mfe_bps_60s is not None]
    mae = [float(item.mae_bps_60s) for item in items if item.mae_bps_60s is not None]
    return {
        "samples": len(items),
        "executable_percent": 100 * sum(item.executable for item in items) / len(items)
        if items
        else 0,
        "mean_net_return_bps": statistics.fmean(net) if net else None,
        "median_net_return_bps": statistics.median(net) if net else None,
        "positive_percent": 100 * sum(value > 0 for value in net) / len(net) if net else None,
        "p10_net_return_bps": _quantile(net, 0.1),
        "p90_net_return_bps": _quantile(net, 0.9),
        "mean_mfe_bps": statistics.fmean(mfe) if mfe else None,
        "mean_mae_bps": statistics.fmean(mae) if mae else None,
        "average_cost_bps": statistics.fmean(costs) if costs else None,
        "average_depth_slippage_bps": statistics.fmean(slip) if slip else None,
    }


def _block_bootstrap(
    blocks: tuple[list[float], ...], *, iterations: int, seed: int
) -> dict[str, object]:
    if len(blocks) < 2 or not any(blocks):
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "block_count": len(blocks),
            "mean_ci95": None,
            "median_ci95": None,
            "positive_fraction_ci95": None,
        }
    rng = random.Random(seed)
    mean_samples: list[float] = []
    median_samples: list[float] = []
    positive_samples: list[float] = []
    for _ in range(iterations):
        sample = [
            value for _index in range(len(blocks)) for value in blocks[rng.randrange(len(blocks))]
        ]
        mean_samples.append(statistics.fmean(sample))
        median_samples.append(statistics.median(sample))
        positive_samples.append(sum(value > 0 for value in sample) / len(sample))
    return {
        "status": "OK",
        "block_count": len(blocks),
        "mean_ci95": [_quantile(mean_samples, 0.025), _quantile(mean_samples, 0.975)],
        "median_ci95": [_quantile(median_samples, 0.025), _quantile(median_samples, 0.975)],
        "positive_fraction_ci95": [
            _quantile(positive_samples, 0.025),
            _quantile(positive_samples, 0.975),
        ],
    }


def _net_values(items: list[ExecutableForwardLabel]) -> list[float]:
    return [float(item.net_return_bps) for item in items if item.net_return_bps is not None]


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _required_quantile(values: list[float], q: float) -> float:
    result = _quantile(values, q)
    return 0.0 if result is None else result


def _bin(value: float, cuts: tuple[float, ...]) -> str:
    return f"Q{1 + sum(value > cut for cut in cuts)}"


def _coarse(value: float, cuts: tuple[float, float]) -> str:
    return "LOW" if value <= cuts[0] else "MID" if value <= cuts[1] else "HIGH"


def _json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("status\nNO_DATA\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
