"""Portfolio snapshot construction for deterministic research runs."""

from datetime import datetime

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import PortfolioSnapshot


def initial_portfolio(config: TradingConfig, captured_at: datetime) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id=f"initial-{captured_at.isoformat()}",
        captured_at=captured_at,
        cash_balance=config.initial_balance,
        equity=config.initial_balance,
        day_start_equity=config.initial_balance,
        daily_loss=config.initial_balance * 0,
        entries_today=0,
        orders_today=0,
        closed_trades_today=0,
        positions=(),
    )
