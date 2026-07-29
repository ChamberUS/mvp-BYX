"""Small helpers shared by CLI backtest commands."""

from adaptive_trader.backtest.engine import BacktestEngine
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.protocols import Repository
from adaptive_trader.execution.backtest import BacktestExecutionConfig, BacktestOrderExecutor
from adaptive_trader.risk.manager import DefaultRiskManager
from adaptive_trader.strategy.deterministic import DeterministicAnalyzer


def build_engine(config: TradingConfig, repository: Repository | None = None) -> BacktestEngine:
    strategy = DeterministicAnalyzer(
        short_period=config.short_ema_period,
        long_period=config.long_ema_period,
        minimum_volume_ratio=config.minimum_volume_ratio,
        maximum_atr_relative=config.maximum_atr_relative,
        stop_atr_multiple=config.stop_atr_multiple,
        target_r_multiple=config.target_r_multiple,
    )
    executor = BacktestOrderExecutor(
        BacktestExecutionConfig(
            maker_fee_bps=config.maker_fee_bps,
            taker_fee_bps=config.taker_fee_bps,
            slippage_bps=config.slippage_bps,
            spread_bps=config.spread_bps,
        )
    )
    return BacktestEngine(
        strategy=strategy,
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=executor,
        config=config,
        repository=repository,
    )
