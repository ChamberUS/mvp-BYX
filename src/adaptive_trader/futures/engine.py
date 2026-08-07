"""Independent deterministic USD-M Futures backtest engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import overload

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.futures.accounting import (
    approximate_liquidation_price,
    funding_cash_flow,
    initial_margin,
    maintenance_margin,
    position_notional,
    unrealized_pnl,
)
from adaptive_trader.futures.integrity import align_mark_prices
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesBacktestConfig,
    FuturesBacktestResult,
    FuturesCandle,
    FuturesDecisionTrace,
    FuturesExitReason,
    FuturesMetrics,
    FuturesOrderIntent,
    FuturesPortfolioState,
    FuturesPosition,
    FuturesPriceSource,
    FuturesSignal,
    FuturesSignalDirection,
    FuturesTrade,
    MarkPriceCandle,
)
from adaptive_trader.futures.risk import FuturesRiskManager
from adaptive_trader.futures.strategy import (
    DeterministicFuturesAnalyzer,
    FuturesMarketAnalyzer,
)


@dataclass(slots=True)
class _State:
    wallet: Decimal
    position: FuturesPosition | None = None
    trades: list[FuturesTrade] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    traces: list[FuturesDecisionTrace] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)
    margin_curve: list[Decimal] = field(default_factory=list)
    leverage_curve: list[Decimal] = field(default_factory=list)
    minimum_margin_ratio: Decimal | None = None
    funding_paid: Decimal = Decimal("0")
    funding_received: Decimal = Decimal("0")
    funding_events: int = 0
    entries_today: int = 0
    day_start_equity: Decimal = Decimal("0")
    current_day: date | None = None
    liquidated_today: bool = False
    candles_since_liquidation: int | None = None
    bankrupt: bool = False
    long_exposure_candles: int = 0
    short_exposure_candles: int = 0
    minimum_free_balance: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _PendingSignal:
    execute_index: int
    signal: FuturesSignal


class _CandlePrefix(Sequence[FuturesCandle]):
    def __init__(
        self,
        candles: tuple[FuturesCandle, ...],
        length: int,
    ) -> None:
        self._candles = candles
        self._length = length

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> FuturesCandle: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[FuturesCandle, ...]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> FuturesCandle | tuple[FuturesCandle, ...]:
        if isinstance(index, slice):
            return tuple(
                self._candles[position]
                for position in range(*index.indices(self._length))
            )
        normalized = index + self._length if index < 0 else index
        if normalized < 0 or normalized >= self._length:
            raise IndexError("Futures candle prefix index out of range")
        return self._candles[normalized]


class FuturesBacktestEngine:
    def __init__(
        self,
        config: FuturesBacktestConfig,
        *,
        analyzer: FuturesMarketAnalyzer | None = None,
        risk_manager: FuturesRiskManager | None = None,
        strategy_version: str = "deterministic-futures-1",
    ) -> None:
        self._config = config
        self._analyzer = analyzer or DeterministicFuturesAnalyzer()
        self._risk = risk_manager or FuturesRiskManager()
        self._strategy_version = strategy_version

    def run(
        self,
        candles: tuple[FuturesCandle, ...],
        mark_prices: tuple[MarkPriceCandle, ...],
        funding_rates: tuple[FundingRate, ...],
    ) -> FuturesBacktestResult:
        self._validate_inputs(candles, mark_prices, funding_rates)
        source_marks = {item.open_time: item for item in mark_prices}
        marks = {
            item.candle_open_time: source_marks[item.mark_open_time]
            for item in align_mark_prices(candles, mark_prices)
            if item.mark_open_time is not None
        }
        state = _State(
            wallet=self._config.initial_balance,
            day_start_equity=self._config.initial_balance,
        )
        state.warnings.extend(
            ("LIQUIDATION_MODEL_APPROXIMATE", "MAINTENANCE_MARGIN_APPROXIMATE")
        )
        if self._config.price_source is FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY:
            state.warnings.extend(("SPOT_PROXY_FOR_TESTS_ONLY", "REPORT_INVALID_PRICE_PROXY"))
        if (
            self._config.funding_enabled
            and not funding_rates
            and self._config.funding_missing_policy is FundingMissingPolicy.WARN_AND_SKIP
        ):
            state.warnings.append("FUNDING_DATA_MISSING")
        pending: _PendingSignal | None = None
        funding_index = 0
        evaluated = candles[self._config.warmup_candles :]
        for absolute_index, candle in enumerate(candles):
            if absolute_index < self._config.warmup_candles:
                continue
            mark = self._mark_for(candle, marks)
            self._roll_day(state, candle.open_time.date())
            if state.candles_since_liquidation is not None:
                state.candles_since_liquidation += 1
            funding_index = self._apply_funding(
                state,
                funding_rates,
                funding_index,
                candle.open_time,
                candle.open_time,
                mark,
            )
            pending_exit: _PendingSignal | None = None
            if pending is not None and pending.execute_index == absolute_index:
                if pending.signal.direction in {
                    FuturesSignalDirection.EXIT_LONG,
                    FuturesSignalDirection.EXIT_SHORT,
                }:
                    pending_exit = pending
                else:
                    self._execute_pending(
                        state,
                        pending.signal,
                        candle,
                        mark,
                        bool(funding_rates),
                    )
                pending = None
            funding_index = self._apply_funding(
                state,
                funding_rates,
                funding_index,
                candle.open_time + timedelta(microseconds=1),
                candle.close_time,
                mark,
            )
            if state.position is not None:
                state.position.holding_candles += 1
                self._update_position(state.position, mark.close)
                exit_reason, exit_price, ambiguous = self._exit_trigger(
                    state.position,
                    candle,
                    mark,
                )
                if exit_reason is not None and exit_price is not None:
                    self._close_position(
                        state,
                        exit_price,
                        candle.close_time,
                        exit_reason,
                        ambiguous=ambiguous,
                    )
                elif (
                    pending_exit is None
                    and self._config.time_exit_candles is not None
                    and state.position.holding_candles >= self._config.time_exit_candles
                ):
                    self._close_position(
                        state,
                        candle.close,
                        candle.close_time,
                        FuturesExitReason.TIME_EXIT,
                    )
            if pending_exit is not None:
                self._execute_pending(
                    state,
                    pending_exit.signal,
                    candle,
                    mark,
                    bool(funding_rates),
                )
            self._record_curves(state, mark.close)
            signal = self._analyzer.analyze(
                _CandlePrefix(candles, absolute_index + 1),
                self._config,
                state.position.side if state.position is not None else None,
            )
            state.traces.append(
                FuturesDecisionTrace(
                    timestamp=candle.close_time,
                    candle_index=absolute_index,
                    signal=signal.direction,
                    reason_code=signal.reason_code,
                    risk_reason_code=None,
                    position_side=state.position.side if state.position is not None else None,
                    mark_price=mark.close,
                    regime=signal.regime,
                )
            )
            if signal.direction is not FuturesSignalDirection.HOLD:
                execute_index = absolute_index + self._config.latency_candles
                if execute_index < len(candles):
                    pending = _PendingSignal(execute_index, signal)
        if state.position is not None and self._config.force_close_at_end:
            last_candle = candles[-1]
            self._close_position(
                state,
                last_candle.close,
                last_candle.close_time,
                FuturesExitReason.FORCED_END,
            )
            self._record_curves(state, self._mark_for(last_candle, marks).close)
        metrics = self._metrics(state, len(evaluated))
        return FuturesBacktestResult(
            report_version="futures-1",
            strategy_version=self._strategy_version,
            market_type=self._config.market_type,
            contract_type=self._config.contract_type,
            trading_mode=self._config.trading_mode,
            leverage=self._config.leverage,
            symbol=self._config.symbol,
            interval=self._config.interval,
            start_time=evaluated[0].open_time,
            end_time=evaluated[-1].open_time,
            input_candle_count=len(candles),
            warmup_candle_count=self._config.warmup_candles,
            evaluated_candle_count=len(evaluated),
            metrics=metrics,
            trades=tuple(state.trades),
            warnings=tuple(dict.fromkeys(state.warnings)),
            equity_curve=tuple(state.equity_curve),
            margin_utilization_curve=tuple(state.margin_curve),
            effective_leverage_curve=tuple(state.leverage_curve),
            decision_traces=tuple(state.traces),
            metadata={
                "research_only": True,
                "authenticated_endpoints_used": False,
                "liquidation_priority": self._config.intrabar_policy,
                "price_source": self._config.price_source,
                "valid_price_source": (
                    self._config.price_source is not FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY
                ),
            },
        )

    def _validate_inputs(
        self,
        candles: tuple[FuturesCandle, ...],
        marks: tuple[MarkPriceCandle, ...],
        funding: tuple[FundingRate, ...],
    ) -> None:
        if len(candles) <= self._config.warmup_candles:
            raise ValueError("futures dataset does not exceed warmup")
        if any(
            current.open_time <= previous.open_time
            for previous, current in zip(candles, candles[1:], strict=False)
        ):
            raise ValueError("futures candles must be strictly chronological")
        if any(not item.is_closed for item in candles):
            raise ValueError("futures backtest requires closed candles")
        if self._config.price_source is not FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY:
            alignment = align_mark_prices(candles, marks)
            missing = tuple(item for item in alignment if item.mark_open_time is None)
            if missing:
                raise ValueError(
                    f"MARK_PRICE_MISSING at {missing[0].candle_open_time.isoformat()}"
                )
            if any(item.future_match for item in alignment):
                raise ValueError("MARK_PRICE_FUTURE_ALIGNMENT")
        if self._config.funding_enabled and not funding:
            if self._config.funding_missing_policy is FundingMissingPolicy.FAIL:
                raise ValueError("FUNDING_DATA_MISSING")

    def _mark_for(
        self,
        candle: FuturesCandle,
        marks: dict[datetime, MarkPriceCandle],
    ) -> MarkPriceCandle:
        found = marks.get(candle.open_time)
        if found is not None:
            return found
        if self._config.price_source is not FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY:
            raise ValueError(f"MARK_PRICE_MISSING at {candle.open_time.isoformat()}")
        return MarkPriceCandle(
            symbol=candle.symbol,
            interval=candle.interval,
            open_time=candle.open_time,
            close_time=candle.close_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            is_closed=candle.is_closed,
        )

    def _execute_pending(
        self,
        state: _State,
        signal: FuturesSignal,
        candle: FuturesCandle,
        mark: MarkPriceCandle,
        funding_available: bool,
    ) -> None:
        if signal.direction in {
            FuturesSignalDirection.EXIT_LONG,
            FuturesSignalDirection.EXIT_SHORT,
        }:
            expected = (
                PositionSide.LONG
                if signal.direction is FuturesSignalDirection.EXIT_LONG
                else PositionSide.SHORT
            )
            if state.position is not None and state.position.side is expected:
                reason = (
                    FuturesExitReason.REGIME_LOSS_EXIT
                    if signal.reason_code == "REGIME_LOSS_EXIT"
                    else FuturesExitReason.MANUAL_SIMULATED_EXIT
                )
                self._close_position(
                    state,
                    candle.open,
                    candle.open_time,
                    reason,
                )
            return
        provisional_side = (
            PositionSide.LONG
            if signal.direction is FuturesSignalDirection.ENTER_LONG
            else PositionSide.SHORT
        )
        execution_price = self._execution_price(candle.open, provisional_side, opening=True)
        equity = state.wallet
        if state.position is not None:
            equity += unrealized_pnl(
                state.position.side,
                state.position.entry_price,
                mark.open,
                state.position.quantity,
            )
        daily_loss = max(Decimal("0"), state.day_start_equity - equity)
        decision = self._risk.evaluate(
            signal,
            FuturesPortfolioState(
                wallet_balance=state.wallet,
                day_start_equity=state.day_start_equity,
                entries_today=state.entries_today,
                daily_loss=daily_loss,
                position_open=state.position is not None,
                candles_since_liquidation=state.candles_since_liquidation,
                liquidated_today=state.liquidated_today,
                kill_state=state.bankrupt or state.wallet < self._config.minimum_wallet_balance,
            ),
            self._config,
            execution_price=execution_price,
            funding_available=(
                funding_available
                or not self._config.funding_enabled
                or self._config.funding_missing_policy is FundingMissingPolicy.WARN_AND_SKIP
            ),
            decided_at=candle.open_time,
        )
        state.traces.append(
            FuturesDecisionTrace(
                timestamp=candle.open_time,
                candle_index=-1,
                signal=signal.direction,
                reason_code=signal.reason_code,
                risk_reason_code=decision.reason_code,
                position_side=state.position.side if state.position else None,
                mark_price=mark.open,
                regime=signal.regime,
            )
        )
        if decision.intent is not None:
            self._open_position(state, decision.intent, execution_price, mark.open)

    def _open_position(
        self,
        state: _State,
        intent: FuturesOrderIntent,
        execution_price: Decimal,
        mark_price: Decimal,
    ) -> None:
        notional = position_notional(execution_price, intent.quantity)
        margin = initial_margin(notional, intent.leverage)
        entry_fee = notional * self._config.taker_fee_bps / Decimal("10000")
        state.wallet -= entry_fee
        free_balance = state.wallet - margin
        state.minimum_free_balance = (
            free_balance
            if state.minimum_free_balance is None
            else min(state.minimum_free_balance, free_balance)
        )
        state.position = FuturesPosition(
            position_id=f"position-{intent.intent_id}",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            entry_price=execution_price,
            mark_price=mark_price,
            notional=position_notional(mark_price, intent.quantity),
            leverage=intent.leverage,
            isolated_margin=margin,
            free_balance_after_entry=free_balance,
            maintenance_margin=maintenance_margin(
                position_notional(mark_price, intent.quantity),
                self._config.maintenance_margin_rate,
            ),
            liquidation_price=approximate_liquidation_price(
                intent.side,
                execution_price,
                intent.leverage,
                self._config.maintenance_margin_rate,
            ),
            unrealized_pnl=unrealized_pnl(
                intent.side, execution_price, mark_price, intent.quantity
            ),
            realized_pnl=Decimal("0"),
            accumulated_funding=Decimal("0"),
            entry_fee=entry_fee,
            opened_at=intent.created_at,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            initial_risk=abs(execution_price - intent.stop_loss) * intent.quantity,
        )
        state.entries_today += 1

    def _apply_funding(
        self,
        state: _State,
        rates: tuple[FundingRate, ...],
        start_index: int,
        candle_start: datetime,
        candle_end: datetime,
        mark: MarkPriceCandle,
    ) -> int:
        index = start_index
        while index < len(rates) and rates[index].funding_time < candle_start:
            index += 1
        while index < len(rates) and rates[index].funding_time <= candle_end:
            event = rates[index]
            if (
                self._config.funding_enabled
                and state.position is not None
                and event.funding_time >= state.position.opened_at
            ):
                event_mark = event.mark_price or mark.open
                cash_flow = funding_cash_flow(
                    state.position.side,
                    position_notional(event_mark, state.position.quantity),
                    event.funding_rate,
                )
                state.wallet += cash_flow
                state.position.accumulated_funding += cash_flow
                state.funding_events += 1
                if cash_flow < 0:
                    state.funding_paid += abs(cash_flow)
                else:
                    state.funding_received += cash_flow
            index += 1
        return index

    def _exit_trigger(
        self,
        position: FuturesPosition,
        candle: FuturesCandle,
        mark: MarkPriceCandle,
    ) -> tuple[FuturesExitReason | None, Decimal | None, bool]:
        if position.side is PositionSide.LONG:
            liquidated = mark.low <= position.liquidation_price
            stop = candle.low <= position.stop_loss
            target = candle.high >= position.take_profit
        else:
            liquidated = mark.high >= position.liquidation_price
            stop = candle.high >= position.stop_loss
            target = candle.low <= position.take_profit
        if liquidated:
            return FuturesExitReason.LIQUIDATION, position.liquidation_price, stop or target
        if stop:
            return FuturesExitReason.STOP_LOSS, position.stop_loss, target
        if target:
            return FuturesExitReason.TAKE_PROFIT, position.take_profit, False
        return None, None, False

    def _close_position(
        self,
        state: _State,
        reference_price: Decimal,
        timestamp: datetime,
        reason: FuturesExitReason,
        *,
        ambiguous: bool = False,
    ) -> None:
        position = state.position
        if position is None:
            return
        if reason is FuturesExitReason.LIQUIDATION:
            exit_price = reference_price
            exit_fee = Decimal("0")
            liquidation_fee = (
                position_notional(exit_price, position.quantity)
                * self._config.liquidation_fee_rate
            )
            if ambiguous:
                state.warnings.append("INTRABAR_LIQUIDATION_AMBIGUOUS")
        else:
            exit_price = self._execution_price(reference_price, position.side, opening=False)
            exit_fee = (
                position_notional(exit_price, position.quantity)
                * self._config.taker_fee_bps
                / Decimal("10000")
            )
            liquidation_fee = Decimal("0")
        gross = unrealized_pnl(
            position.side,
            position.entry_price,
            exit_price,
            position.quantity,
        )
        wallet_before_exit = state.wallet
        state.wallet += gross - exit_fee - liquidation_fee
        if state.wallet < 0:
            state.wallet = Decimal("0")
            state.bankrupt = True
        trading_fees = position.entry_fee + exit_fee
        funding_paid = max(Decimal("0"), -position.accumulated_funding)
        funding_received = max(Decimal("0"), position.accumulated_funding)
        net = gross - trading_fees - liquidation_fee + position.accumulated_funding
        state.trades.append(
            FuturesTrade(
                trade_id=f"trade-{position.position_id}-{timestamp.isoformat()}",
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                leverage=position.leverage,
                entry_time=position.opened_at,
                exit_time=timestamp,
                entry_price=position.entry_price,
                exit_price=exit_price,
                entry_notional=position.entry_price * position.quantity,
                initial_margin=position.isolated_margin,
                free_balance_after_entry=position.free_balance_after_entry,
                gross_pnl=gross,
                net_pnl=net,
                trading_fees=trading_fees,
                liquidation_fee=liquidation_fee,
                funding_paid=funding_paid,
                funding_received=funding_received,
                net_funding=position.accumulated_funding,
                exit_reason=reason,
                holding_candles=position.holding_candles,
                intrabar_ambiguous=ambiguous,
                liquidation_price=position.liquidation_price,
                mark_at_exit=position.mark_price,
                maintenance_margin_at_exit=position.maintenance_margin,
                wallet_before_exit=wallet_before_exit,
                wallet_after_exit=state.wallet,
            )
        )
        if reason is FuturesExitReason.LIQUIDATION:
            state.liquidated_today = True
            state.candles_since_liquidation = 0
            if state.wallet == 0:
                state.bankrupt = True
        state.position = None

    def _update_position(self, position: FuturesPosition, mark_price: Decimal) -> None:
        position.mark_price = mark_price
        position.notional = position_notional(mark_price, position.quantity)
        position.maintenance_margin = maintenance_margin(
            position.notional,
            self._config.maintenance_margin_rate,
        )
        position.unrealized_pnl = unrealized_pnl(
            position.side,
            position.entry_price,
            mark_price,
            position.quantity,
        )

    def _record_curves(self, state: _State, mark_price: Decimal) -> None:
        position = state.position
        if position is None:
            state.equity_curve.append(state.wallet)
            state.margin_curve.append(Decimal("0"))
            state.leverage_curve.append(Decimal("0"))
            return
        self._update_position(position, mark_price)
        equity = max(Decimal("0"), state.wallet + position.unrealized_pnl)
        utilization = (
            position.isolated_margin / equity * Decimal("100")
            if equity > 0
            else Decimal("100")
        )
        effective_leverage = position.notional / equity if equity > 0 else Decimal("0")
        margin_equity = position.isolated_margin + position.unrealized_pnl
        ratio = (
            margin_equity / position.maintenance_margin * Decimal("100")
            if position.maintenance_margin > 0
            else None
        )
        if ratio is not None:
            state.minimum_margin_ratio = (
                ratio
                if state.minimum_margin_ratio is None
                else min(state.minimum_margin_ratio, ratio)
            )
        state.equity_curve.append(equity)
        state.margin_curve.append(utilization)
        state.leverage_curve.append(effective_leverage)
        if position.side is PositionSide.LONG:
            state.long_exposure_candles += 1
        else:
            state.short_exposure_candles += 1

    def _roll_day(self, state: _State, current: date) -> None:
        if state.current_day == current:
            return
        state.current_day = current
        state.entries_today = 0
        state.liquidated_today = False
        state.day_start_equity = state.wallet

    def _execution_price(
        self,
        reference: Decimal,
        side: PositionSide,
        *,
        opening: bool,
    ) -> Decimal:
        buy = (side is PositionSide.LONG and opening) or (
            side is PositionSide.SHORT and not opening
        )
        cost_rate = (
            self._config.spread_bps + self._config.slippage_bps
        ) / Decimal("10000")
        multiplier = Decimal("1") + cost_rate if buy else Decimal("1") - cost_rate
        return reference * multiplier

    def _metrics(self, state: _State, evaluated_candles: int) -> FuturesMetrics:
        trades = tuple(state.trades)
        long_trades = tuple(item for item in trades if item.side is PositionSide.LONG)
        short_trades = tuple(item for item in trades if item.side is PositionSide.SHORT)
        total_margin = sum((item.initial_margin for item in trades), Decimal("0"))
        total_notional = sum(
            (item.entry_price * item.quantity for item in trades),
            Decimal("0"),
        )
        trading_fees = sum((item.trading_fees for item in trades), Decimal("0"))
        liquidation_fees = sum((item.liquidation_fee for item in trades), Decimal("0"))
        return FuturesMetrics(
            initial_wallet=self._config.initial_balance,
            final_wallet=state.wallet,
            gross_pnl=sum((item.gross_pnl for item in trades), Decimal("0")),
            net_pnl=state.wallet - self._config.initial_balance,
            long_pnl=sum((item.net_pnl for item in long_trades), Decimal("0")),
            short_pnl=sum((item.net_pnl for item in short_trades), Decimal("0")),
            funding_paid=state.funding_paid,
            funding_received=state.funding_received,
            net_funding=state.funding_received - state.funding_paid,
            funding_event_count=state.funding_events,
            trading_fees=trading_fees,
            liquidation_fees=liquidation_fees,
            liquidation_count=sum(
                item.exit_reason is FuturesExitReason.LIQUIDATION for item in trades
            ),
            trade_count=len(trades),
            long_trade_count=len(long_trades),
            short_trade_count=len(short_trades),
            long_win_rate=self._win_rate(long_trades),
            short_win_rate=self._win_rate(short_trades),
            average_margin_utilization=self._average(tuple(state.margin_curve)),
            maximum_margin_utilization=max(state.margin_curve, default=Decimal("0")),
            average_effective_leverage=self._average(tuple(state.leverage_curve)),
            maximum_effective_leverage=max(state.leverage_curve, default=Decimal("0")),
            maximum_position_notional=max(
                (item.entry_notional for item in trades),
                default=Decimal("0"),
            ),
            average_initial_margin=self._average(
                tuple(item.initial_margin for item in trades)
            ),
            minimum_free_balance=(
                state.minimum_free_balance
                if state.minimum_free_balance is not None
                else state.wallet
            ),
            return_on_wallet=(
                (state.wallet - self._config.initial_balance)
                / self._config.initial_balance
                * Decimal("100")
            ),
            return_on_notional=(
                (state.wallet - self._config.initial_balance)
                / total_notional
                * Decimal("100")
                if total_notional
                else Decimal("0")
            ),
            maximum_drawdown=self._drawdown(tuple(state.equity_curve)),
            minimum_margin_ratio=state.minimum_margin_ratio,
            margin_call_count=0,
            bankrupt=state.bankrupt,
            depleted=state.wallet < self._config.minimum_wallet_balance,
            exposure_long_percent=(
                Decimal(state.long_exposure_candles)
                / Decimal(evaluated_candles)
                * Decimal("100")
                if evaluated_candles
                else Decimal("0")
            ),
            exposure_short_percent=(
                Decimal(state.short_exposure_candles)
                / Decimal(evaluated_candles)
                * Decimal("100")
                if evaluated_candles
                else Decimal("0")
            ),
            fees_as_percent_of_margin=(
                (trading_fees + liquidation_fees) / total_margin * Decimal("100")
                if total_margin
                else Decimal("0")
            ),
            funding_as_percent_of_margin=(
                (state.funding_received - state.funding_paid)
                / total_margin
                * Decimal("100")
                if total_margin
                else Decimal("0")
            ),
        )

    @staticmethod
    def _average(values: tuple[Decimal, ...]) -> Decimal:
        return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")

    @staticmethod
    def _win_rate(trades: tuple[FuturesTrade, ...]) -> Decimal | None:
        if not trades:
            return None
        return (
            Decimal(sum(item.net_pnl > 0 for item in trades))
            / Decimal(len(trades))
            * Decimal("100")
        )

    @staticmethod
    def _drawdown(curve: tuple[Decimal, ...]) -> Decimal:
        peak = Decimal("0")
        maximum = Decimal("0")
        for equity in curve:
            peak = max(peak, equity)
            if peak > 0:
                maximum = max(maximum, (peak - equity) / peak * Decimal("100"))
        return maximum
