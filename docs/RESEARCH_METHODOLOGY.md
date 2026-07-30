# Research methodology

## Purpose

The `research` layer organizes deterministic backtests. It does not select a production strategy, send orders, or claim profitability.

## Temporal discipline

Candles remain chronological. The holdout flow separates train, validation, and final test periods. Walk-forward creates rolling or expanding train windows followed by later validation windows. The final test period is never used to select parameters.

The consumed test period from `2026-01-01T00:00:00Z` through
`2026-07-01T00:00:00Z` remains available only as an already-consumed historical reference. It is
excluded from diagnostic development/validation, OFAT, ranking, and timeframe choice. Commands
reject overlapping timeframe comparisons and never fetch a missing interval automatically.

Warmup candles may calculate EMA, ATR, and volume indicators. They cannot generate trades, snapshots, positions, equity points, exposure points, or metrics. Each segment records `requested_evaluation_start_time` and `effective_evaluation_start_time`. Validation and test segments use earlier candles as warmup when available; the first train segment may consume its own initial candles as technical warmup and records `WARMUP_REDUCED_EVALUATION_PERIOD`. No future candle is copied into a fold.

## Dataset identity

Datasets reject open candles, duplicates, mixed symbols, mixed intervals, and out-of-order data. Gaps are reported and follow `FAIL`, `WARN`, or `ALLOW` policy; missing candles are never fabricated. A canonical SHA-256 hash identifies the relevant candle content.

## Benchmarks and costs

BUY_AND_HOLD and CASH are references, not optimization targets. BUY_AND_HOLD uses only the first and last evaluated candles and applies the same configured fee, spread, and slippage assumptions to executable entry and exit. Cost scenarios show how results change under conservative assumptions; costs are never optimized.

## Robustness

Local sensitivity evaluates nearby, explicitly bounded parameters. Diagnostics report train/validation gaps, fold consistency, benchmark comparisons, concentration, drawdown, and cost sensitivity. Few trades make every such diagnostic weak. Stability across nearby periods and parameters matters more than one isolated return.

Sprint 3A.2 diagnostics use point-in-time `StrategyDecisionTrace` records. A trace stores the
indicator values, regime, strategy reason code, risk outcome, and execution outcome known at the
decision timestamp. Decision funnels and reason-code distributions come from these traces.
Future returns for HOLD decisions, MFE, MAE, and time-to-excursion values are attached only by an
offline post-event analysis and are never supplied to the strategy.

Entry/exit decomposition keeps current entry rules while comparing bounded stop/target and time
exit scenarios. Cost scenarios use fixed LOW, BASE, HIGH, and STRESS assumptions for every fold
and include consolidated rows. OFAT changes one allowed strategy parameter at a time, preserves
cost assumptions, and is bounded by a maximum combination count.

Detailed regime metrics use the latest point-in-time regime available before each executed entry.
They remain descriptive: the whole-segment drawdown cannot be interpreted as a causal
regime-specific drawdown.

The robustness scorecard and candidate assessment are deterministic research summaries. They
cannot approve production use, cannot enable trading, and become inconclusive when required
evidence is unavailable.

The regime classifier is approximate and uses only candles available up to each evaluated point. It is not a retroactive label and does not establish causal market regimes.

## Overfitting

Overfitting means adapting a configuration to historical noise rather than durable behavior. A small manual grid can still overfit, so selection is fixed by an explicit criterion and happens only on training data. No genetic search, unrestricted search, machine learning, or automatic production approval is implemented.

Manifests record dataset/configuration hashes, effective-period metadata, segment hashes, code metadata when available, costs, split policy, and warnings. The reproducibility hash excludes execution time, absolute paths, and machine identity. The total evaluated candle count sums evaluated candles; overlapping validation folds are intentionally not deduplicated.

Monte Carlo de sequências de trades é opcional e permanece deliberadamente pendente nesta sprint; nenhuma conclusão probabilística é produzida por embaralhamento de candles.

All outputs are research-only backtests. No authenticated endpoint or real order exists in this sprint. Past results do not guarantee future results and are not financial advice.
