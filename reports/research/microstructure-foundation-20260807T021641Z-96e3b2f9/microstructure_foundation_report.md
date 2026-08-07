# Microstructure Foundation — Sprint 4A.1

Research-only intraday foundation for public Binance Spot and USD-M Futures data.
No authentication, account endpoint, Testnet, paper trading, external order or financial
candidate assessment is present.

## Capture

- Session: `data/microstructure/spot/ETHUSDT/2026-08-07/microstructure-20260807T021518Z-spot`
- Events: `5426`
- Completeness: `COMPLETE`
- Public-only payloads: yes

## Order book and replay

The local book uses buffered diff-depth updates, public REST snapshot alignment, explicit
sequence validation and fail-closed resynchronization. Replay uses a deterministic virtual
clock and never sleeps for strategic timers.

Book diagnostic: `{"best_ask": {"price": "1907.17000000", "quantity": "32.03800000"}, "best_bid": {"price": "1907.16000000", "quantity": "5.67970000"}, "last_update_id": 79574197997, "resync_count": 0, "sequence_gap_count": 0, "status": "SYNCHRONIZED", "synchronized": true}`

## Liquidity and features

Executable long exits consume bids; executable short exits consume asks. Mark price remains
restricted to Futures margin, maintenance and liquidation. Spread, microprice, top-5/10/20
depth, imbalance, aggressive flow, OFI, momentum, volatility and freshness are point-in-time.

## Alpha and NO_TRADE

Long and short are different classes, configurations, reason-code families and state. Numeric
thresholds remain `CALIBRATION_REQUIRED`; frequency is diagnostic and never a quota. Conflicting
long/short confirmations resolve to `NO_TRADE_CONFLICT`.

## Elastic Profit Exit

`ELASTIC_300_150_V0` is synthetic and unselected. A new executable-price peak resets 300 ms;
a persistent microstructure reversal requests exit after 150 ms. The hard profit floor and
liquidity failsafe have priority. No blocking sleep or executable mark-price assumption exists.

This foundation does not claim profitability and is not institutional HFT: Python scheduling,
public Internet latency, exchange aggregation and local clocks limit timing precision.
