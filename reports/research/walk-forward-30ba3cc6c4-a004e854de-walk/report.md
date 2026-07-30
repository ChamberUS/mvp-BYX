# Research report

## Identification

- Experiment: `walk-forward` (`walk-forward-30ba3cc6c4-a004e854de-walk`)
- Dataset: `BINANCE-ETHUSDT-1h-30ba3cc6c4e5868f`
- Dataset hash: `30ba3cc6c4e5868feec7887993c361efb0eba7d322c90d02cf29edf1c22a2f3a`
- Reproducibility hash: `e0e9e6074e28ce9d6faffc5af64b1ba619df9eebb43a0709e6a87708695861f1`

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

- Completed folds: 14/14
- Entries: 22; closed trades: 22
- Mean net return: -0.005902004857945757407142857143%
- Mean maximum drawdown: 0.1631506864791936529628209695%
- Positive folds: 21.42857142857142857142857143%

## Benchmarks

- BUY_AND_HOLD: net_return=51.464330069058397045200% costs=67.92598652596029548
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=1.125224932700542268400% costs=54.30471722054577316
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-14.14475230870105056900% costs=50.17281083660505690
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=38.79795217637443117500% costs=64.4985886250568825
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=60.075125793630428162400% costs=70.25598364855718376
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-5.234558118863453635200% costs=52.58382206954536352
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-24.045618070619326527600% costs=47.49373343853265276
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=32.196852685942641492600% costs=62.71239567663585074
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-43.174172135328420659400% costs=42.31773393574206594
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=26.59614846385189962300% costs=61.1968999093100377
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=77.308852096221652694400% costs=74.91926122743473056
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-34.48819350460654176600% costs=44.6680746916541766
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-26.92484036335934296800% costs=46.7146441239342968
- CASH: net_return=0% costs=0
- BUY_AND_HOLD: net_return=-23.236755149436652601400% costs=47.71260389356526014
- CASH: net_return=0% costs=0

## Diagnostics

- Train/validation return gap: -0.19358875010436524786400
- Best-trade concentration: -486.5590191524234921025491923%
- Top-five concentration: -1930.331629884980590572296288%
- Best-day concentration: -486.5590191524234921025491923%
- Positive/negative months: 4/9
- Longest period without a new top: 495 days

## Warnings

- GAPS_DETECTED: count=1 missing=1
- gap policy WARN accepted missing candles without filling
- WARMUP_REDUCED_EVALUATION_PERIOD: requested=2022-01-01T00:00:00+00:00 effective=2022-01-05T04:00:00+00:00
- OUT_OF_SAMPLE_DEGRADATION
- RESULTS_CONCENTRATED

## Limitations

This is research-only backtest output. No real or authenticated orders were sent.
Results are not financial advice and past results do not guarantee future results.
Diagnostics are not proof of profitability, safety, or statistical significance.
