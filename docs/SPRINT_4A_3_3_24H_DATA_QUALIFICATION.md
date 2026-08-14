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

The campaign `ethusdt-futures-intraday-discovery-v1` now contains five operational sessions. Four
are scientifically admitted and one historical session remains rejected without raw rewrite. This
checkpoint added two sessions from clean `main` at commit
`80c2bd48b3d8b4b197845174afee6e1f336e4177`: a complete requested 1,800-second chunk that
contributes 1,798.923 valid seconds, and a safely interrupted follow-up chunk that contributes
36.160 valid seconds. Both have complete provenance, all four streams, synchronized book, zero
gaps/drops/parser errors and deterministic replay.

The scientific checkpoint totals 1,952.860 seconds (0.542461 h), two UTC dates and 306,138 session
events. Stream delivery contains 7,836 aggTrade, 277,221 bookTicker, 19,117 depth and 1,952
markPrice events; the remaining 12 are internal connection/snapshot events. Coverage is 59.016 s
on 2026-08-07 and 1,893.844 s on 2026-08-14. The operational campaign hash is
`016ef99a5001b5468e0d42915ecaf5e7d04a5fa2179f3e120a4b458931411963`. The duration gate still
needs 84,447.140 seconds; the two-date gate is satisfied.

The long chunk recorded recovered liveness incidents for depth and markPrice. Both were classified
as `THRESHOLD_TOO_STRICT`, remained resolved, and caused no gap, resync or invalid book. Admission
therefore remained unchanged and automatic. The recorder config hash changed to
`8a6f459323958517c345c82418f2c16d9e293f4c351d41aa4a9ce6091b561dec` because it includes the
operational requested duration (1,800 s rather than the earlier 60 s); no frozen methodology
changed.

## Scientific decision

Status remains `ENGINEERING_ONLY`, and the phase status is `DATA_COLLECTION_IN_PROGRESS`.
Discovery and confirmation are unavailable; holdout remains `LOCKED`. No economics command was
run and no maker/taker policy, LONG/SHORT side, notional, feature condition, horizon or exit variant
was inspected for selection.

The only valid central answer is **MORE_DATA_REQUIRED**. This is caused by duration below 24 hours,
insufficient independent episodes, insufficient maker observations and unavailable bootstrap and
confirmation. It is neither evidence for YES nor evidence for NO.

## Bundle and continuation

The current operational bundle is
`reports/research/data-collection-phase-20260814T022027Z-016ef99a/`. It includes pre/post
checkpoints, collection manifest, admission and rejection tables, daily/stream coverage, storage,
provenance, replay and methodology-freeze audits. It deliberately contains no financial result and
no discovery-ready snapshot.

The raw root occupies 133.090 MiB, with 150.257 GiB available. Observed storage projects to about
2.323 GiB for 24 h, so collection is not storage constrained. Raw remains ignored and untracked.
No collection-semantic bug was found and no code changed in this checkpoint.

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

The resume counter was corrected in this sprint to sum admitted scientific event-interval duration,
not requested wall-clock duration. While the gate is incomplete, the recorder requests another
full configured chunk instead of creating a tiny residual chunk. Existing raw remains valid and was
not rewritten; this change only prevents an early stop below 86,400 valid seconds.

## Data Collection Phase — continuation at 03:31 UTC

Collection resumed from a clean `main` worktree at commit
`33236a27908d97e4faf933753d4b73ec39d210d5`. One complete requested 1,800-second chunk added
1,798.969 valid seconds and a safely interrupted follow-up chunk added 30.422 seconds. Both were
`COMPLETE` and admitted with complete provenance, four public streams, zero gaps/drops/parser
errors, synchronized books and deterministic replay.

Scientific coverage is now 3,782.251 seconds (1.050625 h), six admitted sessions, two UTC dates
and 579,827 session events. Stream delivery contains 16,502 aggTrade, 522,497 bookTicker, 37,028
depth and 3,782 markPrice events. Date coverage remains asymmetric: 59.016 seconds on 2026-08-07
and 3,723.235 seconds on 2026-08-14. The formal date gate is satisfied, but this distribution is
retained as a temporal-coverage diagnostic.

The complete chunk recorded four recovered liveness incidents: three
`THRESHOLD_TOO_STRICT` and one `NORMAL_NO_UPDATE`, split evenly between depth and markPrice. None
caused a real gap, resync or invalid book, and none remained unresolved. All 11 event files in the
campaign were rechecked against their persisted SHA-256 hashes. Raw occupies 180.145 MiB, with
150.073 GiB available and a current 24-hour projection of about 2.149 GiB.

The campaign hash is
`3eb387258276637bae94be4387f5b16cd76020c20c4c2ea11149af76530165eb`. The six-hour checkpoint
was not reached: 17,817.749 seconds remain to 21,600. The 24-hour gate lacks 82,617.749 seconds.
Status therefore remains `DATA_COLLECTION_IN_PROGRESS`, dataset `ENGINEERING_ONLY`, and answer
`MORE_DATA_REQUIRED`. The current bundle is
`reports/research/data-collection-phase-20260814T033111Z-3eb38725/`. No economics was run and the
holdout remained locked.
