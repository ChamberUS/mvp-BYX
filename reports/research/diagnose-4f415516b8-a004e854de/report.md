# Research report

## Identification

- Experiment: `diagnose` (`diagnose-4f415516b8-a004e854de`)
- Dataset: `BINANCE-ETHUSDT-1h-4f415516b84c4291`
- Dataset hash: `4f415516b84c4291e3922c901c1ffd3d1cb5a819f249c421805f154318aa46bc`
- Reproducibility hash: `d3774f37152337f9d576f4371710be898cc05d4a1505235912f3e7cf892e81c2`

## Dataset and methodology

- Candles: 35063
- Period: 2022-01-01T00:00:00+00:00 -> 2025-12-31T23:59:59.999000+00:00
- Gap policy: WARN
- Each segment reports input, warmup, requested evaluation, and effective evaluation candles.
- Warmup is used only for indicators; it creates no trades, snapshots, or evaluated metrics.
- Equity curves, exposure, and benchmarks start at the effective evaluation start.
- A reduced first segment can shift its effective start when prior history is unavailable.
- The time series is never shuffled; this is a backtest, not a production approval.

## Results

- Completed folds: 2/2
- Entries: 33; closed trades: 33
- Mean net return: -0.26358328848444569141900%
- Mean maximum drawdown: 1.086830485019799302692114946%
- Positive folds: 0%

## Benchmarks

- BUY_AND_HOLD: net_return=-49.478163538551775977600% costs=40.61193545677759776
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=52.744487161956618783600% costs=68.27238453173812164
- CASH: net_return=0% costs=0

## Diagnostics

- Train/validation return gap: -0.31826885141572779504600
- Best-trade concentration: 13.04639243511951089474158309%
- Top-five concentration: 59.30416214404166538289440370%
- Best-day concentration: 27.39692403299900455940572882%
- Positive/negative months: 6/13
- Longest period without a new top: 1188 days

## Warnings

- GAPS_DETECTED: count=1 missing=1
- gap policy WARN accepted missing candles without filling
- WARMUP_REDUCED_EVALUATION_PERIOD: requested=2022-01-01T00:00:00+00:00 effective=2022-01-05T04:00:00+00:00
- CONSUMED_TEST_EXCLUDED: 2026-01-01 00:00:00+00:00 -> 2026-07-01 00:00:00+00:00
- OUT_OF_SAMPLE_DEGRADATION

## Limitations

This is research-only backtest output. No real or authenticated orders were sent.
Results are not financial advice and past results do not guarantee future results.
Diagnostics are not proof of profitability, safety, or statistical significance.
