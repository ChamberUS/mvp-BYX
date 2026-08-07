"""Mirrored pullback-continuation analyzer for USD-M Futures research."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import Candle, MarketRegime
from adaptive_trader.futures.models import (
    FuturesBacktestConfig,
    FuturesCandle,
    FuturesSignal,
    FuturesSignalDirection,
)
from adaptive_trader.indicators import atr, candle_ema, volume_ratio
from adaptive_trader.strategy.pullback import (
    PullbackContinuationCore,
    PullbackDecisionTrace,
    PullbackParameters,
    PullbackReasonCode,
)
from adaptive_trader.strategy.regime import DeterministicRegimeClassifier


class PullbackContinuationFuturesAnalyzer:
    def __init__(self, parameters: PullbackParameters) -> None:
        self.parameters = parameters
        self._core = PullbackContinuationCore(parameters)
        self._traces: list[PullbackDecisionTrace] = []
        self._indicator_candles: list[Candle] = []
        self._cached_first_open_time: datetime | None = None
        self._cached_last_open_time: datetime | None = None
        self._cached_short_ema: Decimal | None = None
        self._cached_long_ema: Decimal | None = None
        self._cached_periods: tuple[int, int] | None = None
        self._classifier: DeterministicRegimeClassifier | None = None
        self._classifier_parameters: tuple[int, int, Decimal] | None = None

    @property
    def traces(self) -> tuple[PullbackDecisionTrace, ...]:
        return tuple(self._traces)

    def analyze(
        self,
        candles: Sequence[FuturesCandle],
        config: FuturesBacktestConfig,
        position_side: PositionSide | None,
    ) -> FuturesSignal:
        latest = candles[-1]
        required = max(
            config.long_ema_period + 5,
            config.atr_period + 1,
            config.volume_period,
        )
        if len(candles) < required:
            return self._hold(latest, PullbackReasonCode.INSUFFICIENT_DATA)
        indicator_candles = self._materialize_incrementally(candles)
        short_ema, long_ema = self._ema_values(indicator_candles, config)
        indicator_window = tuple(indicator_candles[
            -max(config.atr_period + 1, config.volume_period) :
        ])
        atr_value = atr(indicator_window, config.atr_period)
        try:
            relative_volume = volume_ratio(
                indicator_window[-config.volume_period :],
                config.volume_period,
            )
        except ZeroDivisionError:
            return self._hold(latest, PullbackReasonCode.VOLUME_REJECTED)
        regime = self._regime(config).classify(indicator_candles).regime
        if position_side is not None:
            if self.parameters.regime_loss_exit and (
                (
                    position_side is PositionSide.LONG
                    and regime is not MarketRegime.TRENDING_UP
                )
                or (
                    position_side is PositionSide.SHORT
                    and regime is not MarketRegime.TRENDING_DOWN
                )
            ):
                return FuturesSignal(
                    signal_id=(
                        f"{latest.symbol}-{latest.open_time.isoformat()}-"
                        f"EXIT-{position_side.value}"
                    ),
                    symbol=latest.symbol,
                    generated_at=latest.close_time,
                    direction=(
                        FuturesSignalDirection.EXIT_LONG
                        if position_side is PositionSide.LONG
                        else FuturesSignalDirection.EXIT_SHORT
                    ),
                    regime=regime,
                    entry_price=latest.close,
                    stop_loss=None,
                    take_profit=None,
                    rationale="point-in-time regime loss; delayed Futures exit",
                    reason_code=PullbackReasonCode.REGIME_LOSS_EXIT,
                )
            return self._hold(
                latest,
                PullbackReasonCode.POSITION_MANAGED_BY_ENGINE,
                regime,
            )
        evaluation = self._core.evaluate(
            latest=latest.as_indicator_candle(),
            previous=candles[-2].as_indicator_candle(),
            regime=regime,
            short_ema=short_ema,
            long_ema=long_ema,
            atr_value=atr_value,
            volume_ratio=relative_volume,
            allow_long=config.trading_mode
            in {TradingMode.FUTURES_LONG_ONLY, TradingMode.FUTURES_LONG_SHORT},
            allow_short=config.trading_mode
            in {TradingMode.FUTURES_SHORT_ONLY, TradingMode.FUTURES_LONG_SHORT},
        )
        self._traces.append(evaluation.trace)
        if evaluation.direction is None:
            return self._hold(latest, evaluation.trace.reason_code, regime)
        risk = atr_value * self.parameters.stop_atr_multiple
        if evaluation.direction is PositionSide.LONG:
            direction = FuturesSignalDirection.ENTER_LONG
            stop = latest.close - risk
            target = latest.close + risk * self.parameters.target_r_multiple
            reason = PullbackReasonCode.ENTER_LONG_APPROVED
        else:
            direction = FuturesSignalDirection.ENTER_SHORT
            stop = latest.close + risk
            target = latest.close - risk * self.parameters.target_r_multiple
            reason = PullbackReasonCode.ENTER_SHORT_APPROVED
        if stop <= 0 or target <= 0:
            return self._hold(latest, PullbackReasonCode.VOLATILITY_REJECTED, regime)
        return FuturesSignal(
            signal_id=f"{latest.symbol}-{latest.open_time.isoformat()}-{direction.value}",
            symbol=latest.symbol,
            generated_at=latest.close_time,
            direction=direction,
            regime=regime,
            entry_price=latest.close,
            stop_loss=stop,
            take_profit=target,
            rationale="point-in-time mirrored pullback continuation resumption",
            reason_code=reason,
        )

    def _materialize_incrementally(
        self,
        candles: Sequence[FuturesCandle],
    ) -> list[Candle]:
        sequential = (
            len(self._indicator_candles) == len(candles) - 1
            and bool(self._indicator_candles)
            and self._cached_first_open_time == candles[0].open_time
            and self._cached_last_open_time == candles[-2].open_time
        )
        if sequential:
            self._indicator_candles.append(candles[-1].as_indicator_candle())
        else:
            self._indicator_candles = [
                item.as_indicator_candle() for item in candles
            ]
            self._cached_short_ema = None
            self._cached_long_ema = None
        self._cached_first_open_time = candles[0].open_time
        self._cached_last_open_time = candles[-1].open_time
        return self._indicator_candles

    def _ema_values(
        self,
        candles: Sequence[Candle],
        config: FuturesBacktestConfig,
    ) -> tuple[Decimal, Decimal]:
        periods = (config.short_ema_period, config.long_ema_period)
        if (
            self._cached_short_ema is None
            or self._cached_long_ema is None
            or self._cached_periods != periods
        ):
            materialized = tuple(candles)
            short = candle_ema(materialized, config.short_ema_period)
            long = candle_ema(materialized, config.long_ema_period)
        else:
            short_multiplier = Decimal("2") / Decimal(config.short_ema_period + 1)
            long_multiplier = Decimal("2") / Decimal(config.long_ema_period + 1)
            short = (
                (candles[-1].close - self._cached_short_ema) * short_multiplier
                + self._cached_short_ema
            )
            long = (
                (candles[-1].close - self._cached_long_ema) * long_multiplier
                + self._cached_long_ema
            )
        self._cached_short_ema = short
        self._cached_long_ema = long
        self._cached_periods = periods
        return short, long

    def _regime(
        self,
        config: FuturesBacktestConfig,
    ) -> DeterministicRegimeClassifier:
        parameters = (
            config.short_ema_period,
            config.long_ema_period,
            config.maximum_atr_relative,
        )
        if self._classifier is None or self._classifier_parameters != parameters:
            self._classifier = DeterministicRegimeClassifier(
                short_period=config.short_ema_period,
                long_period=config.long_ema_period,
                maximum_atr_relative=config.maximum_atr_relative,
            )
            self._classifier_parameters = parameters
        return self._classifier

    @staticmethod
    def _hold(
        candle: FuturesCandle,
        reason: PullbackReasonCode,
        regime: MarketRegime = MarketRegime.UNKNOWN,
    ) -> FuturesSignal:
        return FuturesSignal(
            signal_id=f"{candle.symbol}-{candle.open_time.isoformat()}-HOLD",
            symbol=candle.symbol,
            generated_at=candle.close_time,
            direction=FuturesSignalDirection.HOLD,
            regime=regime,
            entry_price=candle.close,
            stop_loss=None,
            take_profit=None,
            rationale=reason.value,
            reason_code=reason,
        )
