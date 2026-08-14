# Multi-day execution economics and profit runners

## Scientific boundary

Sprint 4A.3.1 starts a new, non-consumed campaign for maker/taker economics and multi-minute
continuation hypotheses. It does not authenticate, submit orders, use Testnet, exceed leverage 1x,
select an execution policy, tune an alpha threshold or claim profitability.

Campaign `ethusdt-futures-intraday-v1` is now formally `ENGINEERING_CONSUMED`. Its hashes and period
remain useful for regression, documentation, historical comparison and mechanical verification,
but it cannot select a runner, policy, horizon, feature or Alpha V1. New scientific observations
must belong to `ethusdt-futures-intraday-discovery-v1`, must have been recorded after baseline commit
`88d6561`, and cannot share any consumed session event hash.

## Why a multi-minute runner is not a larger Elastic timeout

`ELASTIC_300_150_V0` is a microstructure extension hypothesis. Its 300 ms no-new-peak deadline and
150 ms reversal confirmation remain frozen. `MultiMinuteProfitRunnerController` is a separate
continuation hypothesis: after activation it has no 300 ms timeout, retains the fixed 150 ms
microstructure-reversal confirmation, exits immediately on hard floor or invalid liquidity, and
has an absolute 600 s or 900 s maximum hold. The variants are fixed:

- `IMMEDIATE_PROFIT_EXIT`;
- `ELASTIC_300_150_V0`;
- `MULTI_MINUTE_RUNNER_10M_V0`;
- `MULTI_MINUTE_RUNNER_15M_V0`.

Price, OFI, aggressive-flow, depth and microprice reversal evidence are recorded independently.
The fixed exit rule continues to use microstructure reversal; price reversal alone is diagnostic.
Mark price is ignored for realization. LONG exits use executable bids; SHORT exits use executable
asks.

## Execution policy catalog

[`intraday-execution-policies-v1.toml`](../intraday-execution-policies-v1.toml) freezes exactly four
policies: taker/taker, maker/taker, taker/maker and maker/maker. Maker legs use MakerFirst V0 with a
250 ms wait, conservative FIFO queue, NORMAL latency, 12+3 ms cancel path, cancel-then-taker
remainder fallback, 10 bps maximum slippage and `RESEARCH_FEE_PROFILE_V1`. Fee-only USD-M
round-trip floors are respectively 10, 7, 7 and 4 bps. They are research defaults, not an account
tier claim.

A touch is never a maker fill. Valid evaluation must retain queue ahead, aggressive trades,
partials, cancellation races, expiry, fallback, time-to-fill, queue time, missed opportunity,
adverse selection and fill confidence. If a new campaign cannot support those observations, the
fields remain empty and status is `MORE_DATA_REQUIRED`; lower fees are not treated as achieved
economics.

## Extended labels and capture boundaries

The new horizon catalog is exactly 250/500 ms; 1/3/5/15/30/60 s; and 2/5/10/15 min. A long-horizon
label requires the complete executable interval inside one valid session. A session end,
`CAPTURE_BREAK`, invalid feed/book, missing future state or missing liquidity produces
`LABEL_INCOMPLETE`. No candle, mark price or later session repairs it.

## Non-overlapping episodes and statistics

`NonOverlappingExecutionEpisodeSampler` admits one open hypothetical position per
side × notional × execution policy × exit variant. Anchors arriving while that exact stream is open
are counted and skipped; LONG/SHORT and the other dimensions remain independent. Reports preserve
episode count, effective independent count, skipped anchors and time in market. Frequency and
5–20 trades/day are diagnostics, never quotas.

When data become eligible, episode bootstrap uses deterministic 30-minute temporal blocks, 2,000
iterations and seed 42. A 60/20/20 chronological split is released only at 24 valid hours across at
least two UTC dates; the last 20% remains locked. Confirmation cannot redefine discovery rules.

## CLI and continuation

```bash
adaptive-trader market microstructure campaign-record \
  --market futures --symbol ETHUSDT \
  --campaign-id ethusdt-futures-intraday-discovery-v1 \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --chunk-seconds 1800 --total-seconds 86400 \
  --output-dir data/microstructure

adaptive-trader market microstructure campaign-status \
  --campaign ethusdt-futures-intraday-discovery-v1

adaptive-trader research execution build-multi-day-economics \
  --campaign ethusdt-futures-intraday-discovery-v1 \
  --policies taker-taker,maker-taker,taker-maker,maker-maker \
  --exits immediate,elastic-300-150,runner-10m,runner-15m \
  --notionals 100,500,1000 --latency-profile normal \
  --output-dir reports/research --yes
```

Campaign recording is resumable by requested completed chunk duration, so a small difference
between requested wall time and first/last exchange event does not create a duplicate residual
chunk. Only sessions admitted by the campaign builder enter analysis.

## Current result

The new campaign started with one qualified public ETHUSDT USD-M session: 59.012 seconds, one UTC
date and 10,209 events (281 aggTrade, 9,291 bookTicker, 575 depth, 59 markPrice). Campaign hash is
`b476430b9dda7a155a77cfe02ff095ea8d09326e32a4e73dff56df622840de74`. All 96 availability rows
for 2/5/10/15-minute labels are `LABEL_INCOMPLETE` at the capture boundary. Maker fill rate,
adverse selection, net policy economics, runner episodes, frequency and stability are therefore
not inferred.

The correct result is `ENGINEERING_ONLY`: every policy and runner status is
`MORE_DATA_REQUIRED`. Discovery still needs 86,340.988 valid seconds and at least one additional
UTC date. No Alpha V1 was created.

## Sprint 4A.3.2 provenance correction

The historical 59.012-second raw session remains immutable but is no longer scientifically
admitted because its manifest has `software_commit=UNKNOWN` and no persisted final book status.
The recorder now captures commit, dirty flag, branch, version and config hash before every session.
A new clean 59.016-second session was admitted; scientific progress is therefore 59.016 seconds and
one UTC date, with 86,340.984 seconds and one date still missing. The central qualification remains
`MORE_DATA_REQUIRED`; see `MULTI_DAY_ECONOMIC_QUALIFICATION.md`.

## 24h acquisition checkpoint

Sprint 4A.3.3 added one admitted 58.761-second session without changing this execution methodology.
Scientific totals are 117.777 seconds, two UTC dates and 18,765 events. Duration remains below
`DISCOVERY_READY`, so maker/taker economics, LONG/SHORT, Immediate, Elastic and runner increments
are intentionally null. The exact methodology hashes and resume command are recorded in
`SPRINT_4A_3_3_24H_DATA_QUALIFICATION.md`.
