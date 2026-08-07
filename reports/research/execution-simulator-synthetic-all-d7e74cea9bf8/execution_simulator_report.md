# Intraday Execution Simulator Report

Experiment: `execution-simulator-synthetic-all-d7e74cea9bf8`

This is a research-only mechanical execution diagnostic. It does not send orders,
use authentication, calibrate alpha, or claim profitability.

- Order count: 3
- Deterministic replay: true
- Queue model: conservative FIFO approximation; position is not exact
- Realization basis: executable order-book depth, never mark price alone
- Leverage: locked to 1x
