# Sprint 4A.3.3 — 24h Multi-Day Data Acquisition

## Objective and frozen methodology

This sprint advances the existing public ETHUSDT USD-M Futures campaign without creating a new
feature, horizon, policy, fee profile, notional, runner or threshold. The policy catalog remains
TAKER_TAKER_V1, MAKER_TAKER_V1, TAKER_MAKER_V1 and MAKER_MAKER_V1; notionals remain
100/500/1,000 USDT at 1x; exits remain Immediate, Elastic 300/150 V0 and the frozen 10m/15m
runners. LONG and SHORT remain independent. The holdout was not accessed.

`methodology_freeze.json` records canonical hashes for anchor, features, execution catalog, fees,
NORMAL latency, runners, notionals, horizons, 30-minute/2,000/seed-42 bootstrap and chronological
60/20/20 split. Its status is `FROZEN_UNCHANGED_FROM_SPRINT_4A_3_2`.

## Acquisition checkpoint

The campaign `ethusdt-futures-intraday-discovery-v1` now contains three operational sessions.
Two are scientifically admitted and one historical session remains rejected without raw rewrite.
The new session `microstructure-20260814T011521Z-usd_m_futures` was captured from clean `main` at
commit `bdb02ff1bb3e51d9b754a66e45ab5aff31baf847`. It contributes 58.761 valid seconds and 16,109
events with all four streams, synchronized book, deterministic replay, complete provenance and no
admission reason.

The scientific checkpoint totals 117.777 seconds, two UTC dates and 18,765 events: 499 aggTrade,
16,991 bookTicker, 1,152 depth and 117 markPrice. The eligible campaign hash is
`7fd8c942433071bceb31e76e282310917d996595e9bf097e8c5e8649fc1a6db4`. The duration gate still
needs 86,282.223 seconds; the two-date gate is satisfied.

## Scientific decision

Status remains `ENGINEERING_ONLY`. Discovery and confirmation are unavailable; holdout remains
`LOCKED`. Financial fields for maker/taker policies, LONG/SHORT, notionals and exit variants remain
null. No winner was selected and no intermediate checkpoint was used to inspect edge.

The only valid central answer is **MORE_DATA_REQUIRED**. This is caused by duration below 24 hours,
insufficient independent episodes, insufficient maker observations and unavailable bootstrap and
confirmation. It is neither evidence for YES nor evidence for NO.

## Bundle and continuation

The checkpoint bundle is
`reports/research/24h-multi-day-qualification-20260814T011904Z-7fd8c942/`. It includes the campaign
snapshot, provenance/admission/rejection evidence, methodology hashes, null-safe economics,
Immediate/Elastic/Runner10/Runner15 reports, holdout lock and central answer.

Continue only from a clean worktree. The source of truth is `scientific_valid_duration_seconds`
reported by `campaign-status`, not wall time or requested duration.

```bash
adaptive-trader market microstructure campaign-status \
  --campaign ethusdt-futures-intraday-discovery-v1

adaptive-trader market microstructure campaign-record \
  --market futures \
  --symbol ETHUSDT \
  --campaign-id ethusdt-futures-intraday-discovery-v1 \
  --streams aggTrade,bookTicker,depth,markPrice \
  --depth-speed 100ms \
  --chunk-seconds 1800 \
  --total-seconds 86400 \
  --output-dir data/microstructure
```

Do not run economic selection until the status shows at least 86,400 scientifically valid seconds
and at least two UTC dates. A rejected chunk remains diagnostic raw and never counts toward the
gate.
