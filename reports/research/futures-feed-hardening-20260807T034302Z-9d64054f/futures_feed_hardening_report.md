# Futures Feed Hardening — Sprint 4A.2.1

Readiness: **NOT_READY**

This is a public-market-data, research-only validation. It does not use authentication,
private streams, account endpoints, Testnet, paper trading, external orders, alpha selection,
or leverage above 1x. It makes no profitability claim.

## Routing and delivery

USD-M high-frequency `bookTicker` and `depth@100ms` are routed to `/public`; `aggTrade`
and `markPrice@1s` are routed independently to `/market`. Legacy unrouted WebSocket URLs are
rejected. Feed health is `DEGRADED` and capture quality is `CAPTURE_VALID_WITH_WARNINGS`.

## Previous 30-second smoke

Diagnosis: `LEGACY_ROUTING_AND_CROSS_MARKET_SEQUENCE_POLICY_EXPLAIN_THE_SMOKE`. The earlier implementation requested
MARKET streams through the legacy route and applied Spot post-snapshot contiguity to Futures.
Those are transport/policy defects, not evidence of 66 genuine exchange sequence gaps.

## Order book

- Bootstrap: `U <= lastUpdateId <= u`
- Steady state: `event.pu == previous_event.u`
- Final status: `SYNCHRONIZED`

The 1,800-second capture is permitted only when this report says
`READY_FOR_LONG_CAPTURE`. Otherwise the fail-closed result is `NOT_READY`.
