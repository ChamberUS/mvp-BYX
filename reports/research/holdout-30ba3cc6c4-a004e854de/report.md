# Research report

## Identification

- Experiment: `holdout` (`holdout-30ba3cc6c4-a004e854de`)
- Dataset: `BINANCE-ETHUSDT-1h-30ba3cc6c4e5868f`
- Dataset hash: `30ba3cc6c4e5868feec7887993c361efb0eba7d322c90d02cf29edf1c22a2f3a`
- Reproducibility hash: `302ff27eef4678575946791ab21305f2570035a3e4aa2be7acb73464936c41dc`

## Dataset and methodology

- Candles: 39408
- Period: 2022-01-01T00:00:00+00:00 -> 2026-07-01T00:59:59.999000+00:00
- Gap policy: WARN
- Each segment reports input, warmup, requested evaluation, and effective evaluation candles.
- Warmup is used only for indicators; it creates no trades, snapshots, or evaluated metrics.
- Equity curves, exposure, and benchmarks start at the effective evaluation start.
- A reduced first segment can shift its effective start when prior history is unavailable.
- The time series is never shuffled; this is a backtest, not a production approval.

## Results

- Completed folds: 3/3
- Entries: 36; closed trades: 36
- Mean net return: -0.2379874882513136154033333333%
- Mean maximum drawdown: 0.810583646559954669762345194%
- Positive folds: 33.33333333333333333333333333%

## Benchmarks

- BUY_AND_HOLD: net_return=-38.25851543191411761600% costs=43.64786244741176160
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=52.980124395231849676200% costs=68.33614557511503238
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-56.928275281435119711600% costs=38.59600806411197116
- CASH: net_return=0% costs=0

## Diagnostics

- Train/validation return gap: -0.78823612494908179943400
- Best-trade concentration: -70.10683147759620422016927368%
- Top-five concentration: -269.8119219800719156098353109%
- Best-day concentration: -105.7152160867789940632801116%
- Positive/negative months: 6/14
- Longest period without a new top: 0 days

## Warnings

- GAPS_DETECTED: count=1 missing=1
- gap policy WARN accepted missing candles without filling
- WARMUP_REDUCED_EVALUATION_PERIOD: requested=2022-01-01T00:00:00+00:00 effective=2022-01-05T04:00:00+00:00
- RESULTS_CONCENTRATED

## Limitations

This is research-only backtest output. No real or authenticated orders were sent.
Results are not financial advice and past results do not guarantee future results.
Diagnostics are not proof of profitability, safety, or statistical significance.
