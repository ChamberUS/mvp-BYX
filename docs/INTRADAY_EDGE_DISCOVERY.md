# Intraday edge discovery

## Scientific boundary

Sprint 4A.3 builds an offline dataset and characterizes associations. It does not calibrate
`LongMicrostructureAlpha` or `ShortMicrostructureAlpha`, select a threshold/horizon/notional by
PnL, create a candidate strategy, authenticate, submit orders, use Testnet or exceed leverage
`1x`. Five to twenty closed trades on an active day remains a future diagnostic, never a quota.

The public campaign is the unit of evidence. `MicrostructureCampaignBuilder` admits only complete,
hash-valid, replayable sessions whose feed finishes ready and has no unresolved incident, real
sequence gap, drop or parser corruption. Recovered warnings remain visible. Sessions are ordered,
deduplicated and never filled across their boundaries; time between sessions is `CAPTURE_BREAK`,
not `MARKET_DATA_GAP`.

Dataset sufficiency is fixed before results:

| Status | Minimum valid capture |
| --- | --- |
| `ENGINEERING_ONLY` | 30 minutes |
| `EXPLORATORY` | 6 hours |
| `DISCOVERY_READY` | 24 hours and at least two UTC dates |
| `CONFIRMATION_READY` | 72 hours and at least three UTC dates |

These requirements must not be reduced after inspecting edge. The current one-session campaign is
`ENGINEERING_ONLY`; all financial outputs are diagnostics and the decision is
`MORE_DATA_REQUIRED`.

## Anchors, features and leakage boundary

`MicrostructureAnchorSampler` emits at most one eligible anchor per pre-registered 250 ms event-time
slot. An anchor requires feed `READY`, synchronized uncrossed local book, non-empty depth, warm
features and no unresolved incident. The replay retains session boundaries and does not use
`receive_wall - exchange_event_time` as a feature. That difference is invalid as one-way latency
when clocks are not aligned.

`feature_anchors.csv.gz` contains only information available at `T`: best prices, mid, spread,
depth 5/10/20, executable depth, microprice/edge, imbalances, OFI, aggressive flow, momentum,
volatility and quality ages. Future return, future bid/ask, MFE and MAE are absent. Feature code and
offline label code live in separate modules; alpha imports neither future labels nor holdout data.

## Executable forward labels

The pre-registered horizons are exactly 250 ms, 500 ms, 1 s, 3 s, 5 s, 15 s, 30 s and 60 s.
Notional tiers are exactly 100, 500 and 1,000 USDT. The baseline is `TAKER_ONLY`, deterministic
`NORMAL` arrival latency and `RESEARCH_FEE_PROFILE_V1`; FAST and STRESSED are sensitivities, not
choices. Fee defaults are research assumptions and do not claim the user's account tier.

Long opens by buying asks and closes by selling bids. Short is Futures-only, opens by selling bids
and closes by buying asks. It is simulated independently, never as the negative of long. The
simulator preview validates the requested `PositionEffect`, walks visible depth, retains partial
fills, calculates VWAP and uses the existing `FeeModel`. Missing depth or a missing future state is
`NOT_EXECUTABLE`; no fill is invented. Mark price and candle/mid prices are never realization
prices.

Each label preserves gross return, entry/exit fees, spread cost, depth and latency slippage, total
cost, continuous net return, fill fraction, depth fraction, executable notional and executable
MFE/MAE through 60 s. `NET_POSITIVE`, `NET_ZERO`, `NET_NEGATIVE` and `NOT_EXECUTABLE` are descriptive
flags, not strategy thresholds.

## Temporal protocol and statistics

Only a `DISCOVERY_READY` campaign receives chronological 60/20/20 partitions. Discovery cut points
are frozen and applied unchanged to confirmation. The final 20% is
`LOCKED_FUTURE_HOLDOUT`; its lock records interval, campaign/event hashes, anchor count, commit and
config hash. Discovery APIs reject that partition.

Univariate tables report distributions and quantile bins for long and short independently. Pairwise
analysis is limited to the five pre-registered pairs: OFI 1 s × aggressive flow 1 s, imbalance 10 ×
microprice edge, OFI 3 s × momentum 3 s, spread × depth 10, and volatility 5 s × OFI 1 s. Time of
day, tight/deep regimes and volatility regimes are descriptive; they do not ban hours or create a
strategy. No-trade analysis records contexts where both sides are weak without choosing a gate.

Adjacent anchors are autocorrelated. Confidence intervals therefore use deterministic temporal
block bootstrap with 15-minute blocks, 2,000 iterations and seed 42. This reduces an obvious iid
error but does not establish perfect statistical independence. Association status can be observed
only after discovery and confirmation agree across adjacent horizons, costs and executable fills,
without depending on one hour; it still would not mean a profitable strategy.

## Elastic 300/150

`ELASTIC_300_150_V0` remains fixed and post-event only. Neutral hypothetical 100 USDT entries from
the label dataset are followed through executable books. At activation, immediate executable exit
is compared with the existing controller: 300 ms new-peak timeout, 150 ms confirmed reversal, hard
profit floor and liquidity failsafe. Reports preserve activation, immediate and elastic PnL,
incremental capture, giveback, slippage, adverse selection after exit, hard floors, timeouts,
reversals, failsafes and partial exits. Results classify mechanically as helpful, harmful, mixed or
insufficient; parameters are never rescued after a poor result.

## CLI

```bash
adaptive-trader market microstructure campaign-record \
  --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --campaign-id ethusdt-futures-intraday-v1 \
  --chunk-seconds 1800 --total-seconds 86400 \
  --output-dir data/microstructure

adaptive-trader market microstructure campaign-status \
  --campaign ethusdt-futures-intraday-v1

adaptive-trader research microstructure discover-edge \
  --campaign ethusdt-futures-intraday-v1 --anchor-ms 250 \
  --notionals 100,500,1000 --latency-profile normal \
  --output-dir reports/research --yes

adaptive-trader research microstructure characterize-edge \
  --experiment reports/research/<intraday-edge-discovery-id>
```

Campaign recording is chunked, resumable and signal-safe. Every chunk is an independent public
session; a completed session is not duplicated on resume. Raw captures remain ignored. Derived
feature and label CSVs use deterministic gzip and are verified by SHA-256.

## Sprint 4A.3 observed result

Campaign `ethusdt-futures-intraday-v1` contains the qualified ETHUSDT USD-M session: 1,798.938 s,
one UTC date and 310,393 events. It produced 7,192 anchors. Baseline round trips were executable in
about 99.19% of rows overall, but mean net results were approximately -10.12 bps long and
-9.98 bps short, dominated by the fixed 5 bps taker fee on each leg. These are overlapping
engineering observations, not independent trades or a strategy backtest.

Elastic activated 90 times, all on the independently simulated short side; long had no activation
and is `INSUFFICIENT_SAMPLE` for this diagnostic. Mean immediate result was 5.436 bps, mean Elastic
result 4.980 bps, incremental result -0.456 bps, maximum additional capture 0.103 bps and giveback
0.559 bps. There were 87 300 ms timeout exits, no hard-floor event and no liquidity failsafe; the
combined/short mechanical classification is `MIXED`. Dataset, long and short conclusions remain
`ENGINEERING_ONLY`, `LONG_MORE_DATA_REQUIRED`, `SHORT_MORE_DATA_REQUIRED`, and
`MORE_DATA_REQUIRED`.
