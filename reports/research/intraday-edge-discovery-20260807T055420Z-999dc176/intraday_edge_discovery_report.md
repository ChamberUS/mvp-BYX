# Intraday edge discovery — Sprint 4A.3

## Decision

- DATASET_STATUS: **ENGINEERING_ONLY**
- LONG_STATUS: **LONG_MORE_DATA_REQUIRED**
- SHORT_STATUS: **SHORT_MORE_DATA_REQUIRED**
- ELASTIC_STATUS: **MIXED**
- NEXT_STEP: **MORE_DATA_REQUIRED**

This is dataset engineering and statistical characterization, not alpha calibration or a
profitable-strategy claim. No threshold, horizon, notional or side was selected by PnL.

## Provenance and scope

- Campaign `ethusdt-futures-intraday-v1` contains 1 valid session(s),
  1798.938 seconds and 1 UTC date(s).
- Campaign hash: `999dc176fb76d0cc3f3d85541dc9dbe08333028f163b657813f5a09bec1ac27b`.
- Anchors: 7192 at a pre-registered maximum frequency of 250 ms.
- Horizons: 250ms, 500ms, 1000ms, 3000ms, 5000ms, 15000ms, 30000ms, 60000ms.
- Notionals: 100, 500 and 1000 USDT; leverage 1x.
- Baseline: TAKER_ONLY, NORMAL latency, `RESEARCH_FEE_PROFILE_V1`.
- Long executable rate: 99.19181034482759;
  short: 99.19181034482759.
- Long mean net bps: -10.122470057930025; short: -9.98323407172662.
- Elastic activations: 90 SHORT and 0 LONG; hard floors: 0;
  liquidity failsafes: 0. LONG is `INSUFFICIENT_SAMPLE`; SHORT/combined is `MIXED`.

## Methodological protections

Features and future labels are stored separately and implemented in separate modules. Alpha code
does not import the offline labeler. Long and short execution are independently simulated from
asks/bids; short is not the negation of long. Realization never uses mark price. Quantile bounds are
frozen from discovery before confirmation when the dataset becomes eligible. Discovery rejects the
locked future holdout. The 15-minute, 2,000-iteration, seed-42 block bootstrap does not claim iid
anchors.

`receive_wall_time - exchange_event_time` is explicitly invalid as one-way network latency because
the clocks were not aligned. It is not used by features, labels, alpha or conclusions.

## Interpretation

The available campaign is below 24 valid hours and two UTC dates. All edge, regime, time-of-day,
bootstrap, no-trade and Elastic results are engineering diagnostics only. The valid result is
`MORE_DATA_REQUIRED`; Alpha V1 was not implemented.
