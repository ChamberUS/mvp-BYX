# Multi-Day Economic Qualification — Sprint 4A.3.2

## Scientific question and answer contract

The frozen question is whether a pre-existing microstructure condition combined with one of the
four maker/taker policies produces multi-day net executable edge under conditions accessible to a
small account. The only answers are `YES — ACCESSIBLE_INTRADAY_EDGE_OBSERVED`,
`NO — NO_ACCESSIBLE_INTRADAY_EDGE_OBSERVED`, or `MORE_DATA_REQUIRED`. A 100, 500 or 1,000 USDT
tier at 1x is “feasible” only when execution is possible, costs do not destroy the edge, visible
liquidity is sufficient and fill behavior is plausible; it is not a return promise.

## Provenance and scientific admission

The original 59.012-second raw session is unchanged. Its recorder had never persisted Git metadata,
so it is now explicitly `REJECT_FROM_SCIENTIFIC_DATASET` with `PROVENANCE_INCOMPLETE` (and no
persisted final book status). Every new manifest automatically records the full commit SHA,
`dirty_worktree`, branch, recorder version and canonical recorder-config hash. Failure to read Git
metadata leaves recording operational but marks it `PROVENANCE_INCOMPLETE`; a dirty capture is also
rejected scientifically.

Admission additionally requires COMPLETE, valid event hashes, ETHUSDT USD-M Futures, four live
public streams, no real gap/drop/parser corruption/unresolved incident, synchronized book and a
deterministic replay. Bad chunks remain on disk for diagnosis and do not invalidate admitted chunks.
Intervals between sessions are `CAPTURE_BREAK`; labels, runners and position episodes never cross
them.

## Frozen economics and temporal protocol

The policy catalog remains exactly taker/taker, maker/taker, taker/maker and maker/maker with
MakerFirst 250 ms, conservative FIFO, NORMAL latency, 12+3 ms cancel path, documented fallback,
10 bps maximum slippage and `RESEARCH_FEE_PROFILE_V1`. Round-trip fee floors remain 10/7/7/4 bps.
No anchor, feature, notional, horizon, Elastic 300/150 parameter, 10m/15m runner rule, bootstrap or
alpha threshold changed.

At 24 valid hours across two UTC dates, the pipeline may hash and release a chronological
60% discovery / 20% confirmation / 20% locked-holdout split. Confirmation cannot alter a discovery
rule. Holdout stays `LOCKED` throughout this sprint and cannot select a winner or Alpha V1.
Statistics use non-overlapping episodes and the frozen 30-minute block bootstrap (2,000 iterations,
seed 42). LONG and SHORT remain independent.

## Current evidence

One new session was recorded from clean `main` at commit
`5d2aa404f43fd873d484ba9d1843c6667acfe8f9`: 59.016 valid seconds, 2,656 events and one UTC date.
It has complete provenance, four live streams, synchronized book and deterministic replay. The
scientific campaign therefore remains `ENGINEERING_ONLY`, needs 86,340.984 seconds and one more UTC
date for discovery, and cannot estimate maker fills, taker net economics, LONG/SHORT edge,
frequency, stability, bootstrap confidence or runner increments.

The central answer is **MORE_DATA_REQUIRED**. Winner fields are null, discovery and confirmation
are unavailable, and holdout is locked.

## Safe resumable operation

Run only from a clean worktree. Completed chunks are idempotently counted; raw data remain ignored.

```bash
adaptive-trader market microstructure campaign-record \
  --market futures \
  --symbol ETHUSDT \
  --campaign-id ethusdt-futures-intraday-discovery-v1 \
  --streams aggTrade,bookTicker,depth,markPrice \
  --depth-speed 100ms \
  --chunk-seconds 1800 \
  --total-seconds 86400 \
  --output-dir data/microstructure

adaptive-trader market microstructure campaign-status \
  --campaign ethusdt-futures-intraday-discovery-v1

adaptive-trader research execution build-multi-day-economics \
  --campaign ethusdt-futures-intraday-discovery-v1 \
  --policies taker-taker,maker-taker,taker-maker,maker-maker \
  --exits immediate,elastic-300-150,runner-10m,runner-15m \
  --notionals 100,500,1000 \
  --latency-profile normal \
  --output-dir reports/research \
  --yes
```

The current report is under
`reports/research/multi-day-economic-qualification-20260807T230125Z-88fc458a/`.

## Sprint 4A.3.3 checkpoint

A second clean-provenance session was admitted on 2026-08-14. Scientific coverage is now 117.777
seconds across two UTC dates, with 18,765 events. The date condition is satisfied but the 24-hour
duration condition still lacks 86,282.223 seconds. The new eligible hash is
`7fd8c942433071bceb31e76e282310917d996595e9bf097e8c5e8649fc1a6db4`.

No financial selection was opened: discovery/confirmation remain unavailable, holdout stays
locked, and all policy/notional/runner economics remain null with `MORE_DATA_REQUIRED`. See
`SPRINT_4A_3_3_24H_DATA_QUALIFICATION.md` and the `24h-multi-day-qualification-...` bundle.
