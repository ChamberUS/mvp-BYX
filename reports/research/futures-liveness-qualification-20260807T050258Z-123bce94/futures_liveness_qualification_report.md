# Futures feed liveness qualification

Sprint: `4A.2.2`

Final decision: **READY_FOR_4A3**

## Safety boundary

This qualification used public USD-M market data only. It did not authenticate,
use private streams or Testnet, submit orders, enable paper trading, change alpha,
analyze profitability, or use leverage above 1x.

## Interpretation

Binance's advertised stream update speed is not treated as a mandatory heartbeat.
`markPrice@1s` is approximately periodic; depth and bookTicker are change-driven,
and aggTrade is execution-driven. Depth silence is therefore distinct from a broken
`pu` sequence chain. Recovered incidents remain session warnings but do not leave
current health permanently degraded.

The previous 2.372 s receive-time incident is classified **INCONCLUSIVE**.
Its following depth update preserved the Futures `pu == previous u` chain, but the old
capture lacked event-loop and ping/pong instrumentation, so a narrower cause was not
invented.

## Qualification smoke

- Duration: 299.022194528 seconds
- Events: 37711
- Current health: READY
- Readiness: {'status': 'READY_FOR_LONG_CAPTURE', 'current_health': 'READY', 'session_quality': 'CLEAN', 'reasons': ['OBJECTIVE_QUALITY_BUDGETS_SATISFIED'], 'warning_count': 0, 'all_required_streams_active': True, 'replay_deterministic': True, 'runtime_queue_healthy': True, 'duration_requirement_met': True}

## Long capture

- Executed: True
- Duration: 1798.662933932 seconds
- Events: 310393
- Gaps: 0
- Drops: 0

## Objective assessment

```json
{
  "final": {
    "all_required_streams_active": true,
    "current_health": "READY",
    "duration_requirement_met": true,
    "reasons": [
      "OBJECTIVE_QUALITY_BUDGETS_SATISFIED"
    ],
    "replay_deterministic": true,
    "runtime_queue_healthy": true,
    "session_quality": "CLEAN",
    "status": "READY_FOR_LONG_CAPTURE",
    "warning_count": 0
  },
  "historical_recovered_incidents_are_not_permanent_failures": true,
  "quality_budgets": {
    "maximum_book_invalid_duration_ms": 0,
    "maximum_dropped_events": 0,
    "maximum_parser_errors": 0,
    "maximum_processing_backlog": 5000,
    "maximum_real_sequence_gaps": 0,
    "maximum_recovered_stale_duration_ms": 10000,
    "maximum_unresolved_incidents": 0,
    "source": "ENGINEERING_ASSUMPTION"
  },
  "ready_for_4a3": "READY_FOR_4A3",
  "smoke": {
    "all_required_streams_active": true,
    "current_health": "READY",
    "duration_requirement_met": true,
    "reasons": [
      "OBJECTIVE_QUALITY_BUDGETS_SATISFIED"
    ],
    "replay_deterministic": true,
    "runtime_queue_healthy": true,
    "session_quality": "CLEAN",
    "status": "READY_FOR_LONG_CAPTURE",
    "warning_count": 0
  }
}
```

All latency fields distinguish exchange-event/receive transport timing from local
parsing, book update, queueing, and persistence timing. No strategic or order latency
is inferred from these measurements.
