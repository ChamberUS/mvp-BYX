# Research report

## Identification

- Experiment: `diagnose` (`diagnose-d3e3653ff3-a004e854de`)
- Dataset: `BINANCE-ETHUSDT-1h-d3e3653ff3f0deac`
- Dataset hash: `d3e3653ff3f0deac8b592133c138662fd69b15927f5adaf7136ac29b68c2adae`
- Reproducibility hash: `bbf87d541eac5a62477140467176e7f129986aa18fc2740a0459973bd286c65c`

## Dataset and methodology

- Candles: 4344
- Period: 2025-01-01T00:00:00+00:00 -> 2025-06-30T23:59:59.999000+00:00
- Gap policy: WARN
- Each segment reports input, warmup, requested evaluation, and effective evaluation candles.
- Warmup is used only for indicators; it creates no trades, snapshots, or evaluated metrics.
- Equity curves, exposure, and benchmarks start at the effective evaluation start.
- A reduced first segment can shift its effective start when prior history is unavailable.
- The time series is never shuffled; this is a backtest, not a production approval.

## Results

- Completed folds: 2/2
- Entries: 6; closed trades: 6
- Mean net return: 0.21684022566992397194200%
- Mean maximum drawdown: 0.2780325376585851790318094886%
- Positive folds: 50.0%

## Benchmarks

- BUY_AND_HOLD: net_return=-31.19319768111917122800% costs=45.5596683099171228
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-1.768479515971373510400% costs=53.52170910353735104
- CASH: net_return=0% costs=0

## Diagnostics

- Train/validation return gap: 0.78249032390811648664400
- Best-trade concentration: 92.70289126890822383727765583%
- Top-five concentration: 146.5597804599313049169511820%
- Best-day concentration: 92.70289126890822383727765583%
- Positive/negative months: 1/2
- Longest period without a new top: 14 days

## Warnings

- WARMUP_REDUCED_EVALUATION_PERIOD: requested=2025-01-01T00:00:00+00:00 effective=2025-01-05T04:00:00+00:00
- CONSUMED_TEST_EXCLUDED: 2026-01-01 00:00:00+00:00 -> 2026-07-01 00:00:00+00:00
- OUT_OF_SAMPLE_DEGRADATION
- RESULTS_CONCENTRATED

## Limitations

This is research-only backtest output. No real or authenticated orders were sent.
Results are not financial advice and past results do not guarantee future results.
Diagnostics are not proof of profitability, safety, or statistical significance.
