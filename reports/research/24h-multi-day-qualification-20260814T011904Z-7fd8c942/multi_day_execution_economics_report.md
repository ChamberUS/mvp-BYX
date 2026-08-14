# 24h multi-day data acquisition — Sprint 4A.3.3

## Decision

- DATASET_STATUS: **ENGINEERING_ONLY**
- EXECUTION_POLICY_STATUS LONG/SHORT: **MORE_DATA_REQUIRED**
- RUNNER_STATUS LONG/SHORT and every variant: **MORE_DATA_REQUIRED**
- NEXT_STEP: **MORE_DATA_REQUIRED**
- CENTRAL_ANSWER: **MORE_DATA_REQUIRED**

The prior campaign is formally `ENGINEERING_CONSUMED` and excluded from selection. Campaign
`ethusdt-futures-intraday-discovery-v1` contains 2 new session(s),
117.777 valid seconds and
2 UTC date(s). It remains below the complete discovery gate;
the duration is below 24 hours even when the two-date condition is already satisfied.

All 192 extended-horizon availability rows are `LABEL_INCOMPLETE`; no candle, mark price,
capture break or consumed session was used to fill missing future state. The four immutable policies
have catalog hash `8bf28d3e52c5b2b4aa0f098664673f11d20fa15aae02820edcef3d4d2296b7f8`. Their fee-only round-trip floors are 10 bps taker/taker,
7 bps maker/taker or taker/maker, and 4 bps maker/maker. Maker fill quality, adverse selection and
net economics are not inferred without valid new executable episodes.

Elastic 300/150 remains unchanged. The 10m and 15m runners are independent controllers with fixed
150ms microstructure-reversal confirmation, hard floor, liquidity failsafe and 600/900 second max
hold. Non-overlapping sampling is mandatory per side/notional/policy/exit stream. No runner or
execution policy was selected.

Discovery still needs 86282.223 seconds and
0 additional UTC date(s). Alpha V1 was not created.
