# Pullback Continuation Hypothesis — Sprint 3B.1

## 1. Hypothesis

Research-only continuation after a point-in-time controlled pullback on ETHUSDT 1h.
No statement in this report is a guarantee of profit.

## 2. Diagnostic Motivation

Prior diagnostics associated persistent TRENDING_UP/TRENDING_DOWN regimes with better
historical outcomes and transitions to RANGING with worse outcomes. This is association,
not demonstrated causality.

## 3. Periods Used

- Development: 2022-01-01 through 2023-12-31.
- Locked validation: 2024-01-01 through 2024-12-31.
- Future information was never supplied to an analyzer.
- Spot gap count: 1. Details: ({'missing_open_time': datetime.datetime(2023, 3, 24, 13, 0, tzinfo=datetime.timezone.utc), 'previous_open_time': datetime.datetime(2023, 3, 24, 12, 0, tzinfo=datetime.timezone.utc), 'next_open_time': datetime.datetime(2023, 3, 24, 14, 0, tzinfo=datetime.timezone.utc), 'explanation': 'Candle absent from the persisted public Spot dataset; not fabricated or forward-filled; accepted under WARN.'},). Missing candles were not fabricated.

## 4. Consumed Periods Excluded

2025-01-01 through 2026-07-01 was neither loaded nor backtested. It appears only as an
explicitly excluded consumed reference in the manifest.

## 5. Fixed Catalog

- Canonical hash: `49301c90ded03eb83da6245e47b824931f11a7afdf6e427f88ca809362d42d8b`
- File SHA-256: `1abeb7df0f120132702aabd3ce99a540115184157df95832a0c940308cc2d309`
- `ORIGINAL_BASELINE`: analyzer=ORIGINAL, persistence=0, time_exit=None, regime_loss_exit=False
- `PULLBACK_BASE`: analyzer=PULLBACK, persistence=3, time_exit=None, regime_loss_exit=False
- `PULLBACK_PERSISTENCE_6`: analyzer=PULLBACK, persistence=6, time_exit=None, regime_loss_exit=False
- `PULLBACK_TIME_EXIT_24`: analyzer=PULLBACK, persistence=3, time_exit=24, regime_loss_exit=False
- `PULLBACK_REGIME_LOSS_EXIT`: analyzer=PULLBACK, persistence=3, time_exit=None, regime_loss_exit=True
- `PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT`: analyzer=PULLBACK, persistence=6, time_exit=None, regime_loss_exit=True

## 6. Decision Funnel

- SPOT/LONG/ORIGINAL_BASELINE/DEVELOPMENT: pullbacks=0, resumptions=0, trades=18
- SPOT/LONG/PULLBACK_BASE/DEVELOPMENT: pullbacks=15, resumptions=4, trades=0
- SPOT/LONG/PULLBACK_PERSISTENCE_6/DEVELOPMENT: pullbacks=0, resumptions=0, trades=0
- SPOT/LONG/PULLBACK_TIME_EXIT_24/DEVELOPMENT: pullbacks=15, resumptions=4, trades=0
- SPOT/LONG/PULLBACK_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=15, resumptions=4, trades=0
- SPOT/LONG/PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=0, resumptions=0, trades=0
- FUTURES/LONG/ORIGINAL_BASELINE/DEVELOPMENT: pullbacks=0, resumptions=0, trades=17
- FUTURES/LONG/PULLBACK_BASE/DEVELOPMENT: pullbacks=15, resumptions=4, trades=0
- FUTURES/LONG/PULLBACK_PERSISTENCE_6/DEVELOPMENT: pullbacks=0, resumptions=0, trades=0
- FUTURES/LONG/PULLBACK_TIME_EXIT_24/DEVELOPMENT: pullbacks=15, resumptions=4, trades=0
- FUTURES/LONG/PULLBACK_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=15, resumptions=4, trades=0
- FUTURES/LONG/PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=0, resumptions=0, trades=0
- FUTURES/SHORT/ORIGINAL_BASELINE/DEVELOPMENT: pullbacks=0, resumptions=0, trades=41
- FUTURES/SHORT/PULLBACK_BASE/DEVELOPMENT: pullbacks=30, resumptions=10, trades=0
- FUTURES/SHORT/PULLBACK_PERSISTENCE_6/DEVELOPMENT: pullbacks=12, resumptions=1, trades=0
- FUTURES/SHORT/PULLBACK_TIME_EXIT_24/DEVELOPMENT: pullbacks=30, resumptions=10, trades=0
- FUTURES/SHORT/PULLBACK_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=30, resumptions=10, trades=0
- FUTURES/SHORT/PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=12, resumptions=1, trades=0
- FUTURES/LONG_SHORT/ORIGINAL_BASELINE/DEVELOPMENT: pullbacks=0, resumptions=0, trades=58
- FUTURES/LONG_SHORT/PULLBACK_BASE/DEVELOPMENT: pullbacks=42, resumptions=14, trades=0
- FUTURES/LONG_SHORT/PULLBACK_PERSISTENCE_6/DEVELOPMENT: pullbacks=11, resumptions=1, trades=0
- FUTURES/LONG_SHORT/PULLBACK_TIME_EXIT_24/DEVELOPMENT: pullbacks=42, resumptions=14, trades=0
- FUTURES/LONG_SHORT/PULLBACK_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=42, resumptions=14, trades=0
- FUTURES/LONG_SHORT/PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT/DEVELOPMENT: pullbacks=11, resumptions=1, trades=0
- SPOT/LONG/ORIGINAL_BASELINE/VALIDATION: pullbacks=0, resumptions=0, trades=4
- FUTURES/LONG/ORIGINAL_BASELINE/VALIDATION: pullbacks=0, resumptions=0, trades=4
- FUTURES/SHORT/ORIGINAL_BASELINE/VALIDATION: pullbacks=0, resumptions=0, trades=19
- FUTURES/LONG_SHORT/ORIGINAL_BASELINE/VALIDATION: pullbacks=0, resumptions=0, trades=23

## 7. Original Baseline

The existing deterministic analyzer remains unchanged and is included only as a reference.

## 8. Pullback Base

Requires three persistent trend candles, a one-to-six candle pullback between 0.10 and
1.0 ATR, and close-confirmed resumption with at most 1.0 ATR long-EMA extension.

## 9. Persistence 6

Uses the same fixed rules with six trend-persistence candles.

## 10. Time Exit

Uses the base pullback and one fixed 24-candle time exit.

## 11. Regime-Loss Exit

Detects regime loss only at close and executes no earlier than the next candle open.
Protective stop/target and Futures liquidation retain priority.

## 12. Spot

Spot is long-only, without leverage, margin, short selling, balance transfer, or real orders.

## 13. Futures Long

USD-M Futures long research used isolated 1x simulation and real stored funding.

## 14. Futures Short

Short logic mirrors the long setup semantically, while reporting outcomes independently.

## 15. Futures Long-Short

Long and short signals share one isolated simulated wallet and never hedge simultaneously.

## 16. Development

| Market | Mode | Variant | Net return % | Drawdown % | Trades |
|---|---|---|---:|---:|---:|
| SPOT | LONG | ORIGINAL_BASELINE | -0.87552678886956303640600 | 1.270470357127358153094159219 | 18 |
| SPOT | LONG | PULLBACK_BASE | 0 | 0 | 0 |
| SPOT | LONG | PULLBACK_PERSISTENCE_6 | 0 | 0 | 0 |
| SPOT | LONG | PULLBACK_TIME_EXIT_24 | 0 | 0 | 0 |
| SPOT | LONG | PULLBACK_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| SPOT | LONG | PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| FUTURES | LONG | ORIGINAL_BASELINE | -4.055206676033899961909566270 | 5.277538262445041801311636570 | 17 |
| FUTURES | LONG | PULLBACK_BASE | 0 | 0 | 0 |
| FUTURES | LONG | PULLBACK_PERSISTENCE_6 | 0 | 0 | 0 |
| FUTURES | LONG | PULLBACK_TIME_EXIT_24 | 0 | 0 | 0 |
| FUTURES | LONG | PULLBACK_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| FUTURES | LONG | PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| FUTURES | SHORT | ORIGINAL_BASELINE | 1.292357182881505843009123200 | 6.110751809197362532085945275 | 41 |
| FUTURES | SHORT | PULLBACK_BASE | 0 | 0 | 0 |
| FUTURES | SHORT | PULLBACK_PERSISTENCE_6 | 0 | 0 | 0 |
| FUTURES | SHORT | PULLBACK_TIME_EXIT_24 | 0 | 0 | 0 |
| FUTURES | SHORT | PULLBACK_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| FUTURES | SHORT | PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| FUTURES | LONG_SHORT | ORIGINAL_BASELINE | -2.815257259058395221849622040 | 9.918154880241676708855790223 | 58 |
| FUTURES | LONG_SHORT | PULLBACK_BASE | 0 | 0 | 0 |
| FUTURES | LONG_SHORT | PULLBACK_PERSISTENCE_6 | 0 | 0 | 0 |
| FUTURES | LONG_SHORT | PULLBACK_TIME_EXIT_24 | 0 | 0 | 0 |
| FUTURES | LONG_SHORT | PULLBACK_REGIME_LOSS_EXIT | 0 | 0 | 0 |
| FUTURES | LONG_SHORT | PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT | 0 | 0 | 0 |

Selection:
- SPOT/LONG: NO_DEVELOPMENT_HYPOTHESIS; selected=none
- FUTURES/LONG: NO_DEVELOPMENT_HYPOTHESIS; selected=none
- FUTURES/SHORT: NO_DEVELOPMENT_HYPOTHESIS; selected=none
- FUTURES/LONG_SHORT: NO_DEVELOPMENT_HYPOTHESIS; selected=none

## 17. Validation

| Market | Mode | Variant | Net return % | Drawdown % | Trades |
|---|---|---|---:|---:|---:|
| SPOT | LONG | ORIGINAL_BASELINE | 0.4331918068956389509200 | 0.2442956003932655760284189416 | 4 |
| FUTURES | LONG | ORIGINAL_BASELINE | 1.488628297472222966890500200 | 2.060078761019413073987926039 | 4 |
| FUTURES | SHORT | ORIGINAL_BASELINE | -7.077468050821501345972795910 | 8.001728477562098616341277496 | 19 |
| FUTURES | LONG_SHORT | ORIGINAL_BASELINE | -5.694196958089207008226396980 | 7.864087305885190018687724051 | 23 |

Only baseline and up to two development-qualified variants per market/mode were evaluated.
All validation locks remained unchanged.

## 18. Costs

LOW, BASE, HIGH, and STRESS were executed. Funding was unchanged across scenarios.
Warnings: STRESS_COLLAPSE.

## 19. Funding

Funding is reported separately from trading fees and was applied only to open Futures
positions from the persisted public dataset.

## 20. Concentration

Top-one, top-three, and top-five positive-trade concentration plus results excluding the
best trades are available in `concentration_analysis.csv`.

## 21. Bootstrap

Closed trades were resampled deterministically with seed 42, 2,000 iterations, and 95%
intervals. Candles were never bootstrapped.

## 22. Classification

- SPOT/LONG/NONE: NO_DEVELOPMENT_HYPOTHESIS
- FUTURES/LONG/NONE: NO_DEVELOPMENT_HYPOTHESIS
- FUTURES/SHORT/NONE: NO_DEVELOPMENT_HYPOTHESIS
- FUTURES/LONG_SHORT/NONE: NO_DEVELOPMENT_HYPOTHESIS

No candidate was frozen in this sprint.

## 23. Future Holdout Plan

Status: `NO_HOLDOUT_PLAN`. Any future plan is plan-only, starts strictly after
2026-07-01, requires at least 90 calendar days and 20 closed trades, and restarts after
any configuration change.

## 24. Limitations

This is a deterministic historical simulation with approximate execution, maintenance
margin, and liquidation mechanics. Historical association is not causal evidence; costs,
latency, liquidity, regime classification, and future market structure may differ.
No network, authentication, download, paper trading, Testnet, or external order path ran.
