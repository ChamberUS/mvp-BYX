"""Chronological backtest engine with no access to future candles by strategy."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from adaptive_trader.backtest.metrics import calculate_metrics
from adaptive_trader.backtest.models import BacktestResult, TradeRecord
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    Candle,
    Fill,
    MarketRegime,
    MarketSignal,
    PortfolioSnapshot,
    Position,
    SignalDirection,
    StrategyDecisionRecord,
)
from adaptive_trader.domain.protocols import MarketAnalyzer, Repository, RiskManager
from adaptive_trader.execution.backtest import BacktestOrderExecutor
from adaptive_trader.indicators import atr
from adaptive_trader.market_data.context import MarketContextBuilder


class BacktestEngine:
    def __init__(
        self,
        *,
        strategy: MarketAnalyzer,
        risk_manager: RiskManager,
        executor: BacktestOrderExecutor,
        config: TradingConfig,
        repository: Repository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._executor = executor
        self._config = config
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._context_builder = MarketContextBuilder(
            minimum_candles=1,
            short_ema_period=config.short_ema_period,
            long_ema_period=config.long_ema_period,
            atr_period=config.atr_period,
            volume_period=config.volume_period,
        )

    def run(self, candles: Sequence[Candle]) -> BacktestResult:
        ordered = tuple(candles)
        self._validate_candles(ordered)
        symbol = ordered[0].symbol
        interval = ordered[0].interval
        initial_capital = self._config.initial_balance
        cash = initial_capital
        position: Position | None = None
        pending = None
        pending_signal_time: datetime | None = None
        pending_execution_index: int | None = None
        entry_candle_index: int | None = None
        trades: list[TradeRecord] = []
        warnings: list[str] = [
            "BACKTEST_ONLY: no real orders were sent",
            "execution uses simulated fees, spread and slippage",
            "ambiguous intrabar policy is STOP_FIRST",
        ]
        equity_curve: list[Decimal] = [initial_capital]
        exposure_curve: list[Decimal] = [Decimal("0")]
        entry_count = 0
        order_count = 0
        closed_trade_count = 0
        partial_exit_count = 0
        current_day = ordered[0].open_time.astimezone(UTC).date()
        day_start_equity = initial_capital
        entries_today = 0
        orders_today = 0
        closed_trades_today = 0
        for index, candle in enumerate(ordered):
            candle_day = candle.open_time.astimezone(UTC).date()
            if candle_day != current_day:
                previous_candle = ordered[index - 1]
                day_start_equity = self._equity(cash, position, previous_candle.close)
                entries_today = 0
                orders_today = 0
                closed_trades_today = 0
                current_day = candle_day
            if (
                pending is not None
                and pending_execution_index is not None
                and index >= pending_execution_index
            ):
                if pending_signal_time is None or index == 0:
                    raise RuntimeError("pending order lost its decision timestamp")
                if pending.direction is SignalDirection.BUY:
                    estimated_cost = self._executor.estimate_buy_cost(pending, candle.open)
                    if estimated_cost > cash:
                        warnings.append(
                            "entry skipped at "
                            f"{candle.open_time.isoformat()}: effective cost exceeds cash"
                        )
                        pending = None
                        pending_execution_index = None
                    else:
                        self._executor.set_reference_price(candle.open)
                        order = self._executor.execute(pending)
                        fill = self._fill(order, candle.open_time)
                        total_cost = order.price * order.quantity + order.fee
                        if total_cost > cash:
                            warnings.append(
                                "entry skipped at "
                                f"{candle.open_time.isoformat()}: effective cost exceeds cash"
                            )
                            pending = None
                            pending_execution_index = None
                        elif order.price <= pending.stop_loss:
                            warnings.append(
                                "entry skipped at "
                                f"{candle.open_time.isoformat()}: gap invalidated stop"
                            )
                            pending = None
                            pending_execution_index = None
                        else:
                            cash -= total_cost
                            if cash < 0:
                                raise RuntimeError("backtest cash balance became negative")
                            position = Position(
                                position_id=f"{order.order_id}-POSITION",
                                symbol=order.symbol,
                                quantity=order.quantity,
                                average_entry_price=order.price,
                                current_price=order.price,
                                opened_at=candle.open_time,
                                stop_loss=pending.stop_loss,
                                take_profit=pending.take_profit,
                                initial_risk=order.price - pending.stop_loss,
                                entry_fee=order.fee,
                                partial_taken=False,
                            )
                            entry_candle_index = index
                            entry_count += 1
                            order_count += 1
                            orders_today += 1
                            entries_today += 1
                            self._save_order_fill_position(order, fill, position)
                            pending = None
                            pending_execution_index = None
                else:
                    raise RuntimeError("only BUY entries can be scheduled")
            # Process old protective levels first; close-based protection applies next candle.
            if position is not None:
                position = Position(
                    position_id=position.position_id,
                    symbol=position.symbol,
                    quantity=position.quantity,
                    average_entry_price=position.average_entry_price,
                    current_price=candle.open,
                    opened_at=position.opened_at,
                    stop_loss=position.stop_loss,
                    take_profit=position.take_profit,
                    initial_risk=position.initial_risk,
                    entry_fee=position.entry_fee,
                    partial_taken=position.partial_taken,
                )
                position, cash, partial_orders, partial_trades, partial_exits = (
                    self._manage_position_extensions(
                        position,
                        cash,
                        ordered[: index + 1],
                        candle,
                        index,
                        trades,
                        day_start_equity,
                        entries_today,
                        orders_today,
                        closed_trades_today,
                        entry_candle_index,
                    )
                )
                order_count += partial_orders
                closed_trade_count += partial_trades
                partial_exit_count += partial_exits
                orders_today += partial_orders
                closed_trades_today += partial_trades
                exit_reason, trigger, ambiguous = self._exit_trigger(position, candle)
                if trigger is not None and exit_reason is not None:
                    portfolio = self._snapshot(
                        cash,
                        position,
                        candle,
                        day_start_equity,
                        entries_today,
                        orders_today,
                        closed_trades_today,
                    )
                    exit_signal = self._exit_signal(position, trigger, candle, exit_reason, index)
                    decision = self._risk_manager.evaluate(exit_signal, portfolio, self._config)
                    self._save_risk(decision)
                    if decision.approved and decision.order_intent is not None:
                        self._executor.set_reference_price(trigger)
                        order = self._executor.execute(decision.order_intent)
                        fill = self._fill(order, candle.open_time)
                        cash += order.price * order.quantity - order.fee
                        if entry_candle_index is None:
                            raise RuntimeError("position lost its entry candle index")
                        trade = self._trade_record(
                            position,
                            order,
                            fill,
                            candle,
                            exit_reason,
                            ambiguous,
                            index,
                            entry_candle_index,
                        )
                        trades.append(trade)
                        order_count += 1
                        closed_trade_count += 1
                        orders_today += 1
                        closed_trades_today += 1
                        self._save_order_fill_position(order, fill, None)
                        position = None
                        entry_candle_index = None
                if position is not None:
                    position = self._update_position_protection(
                        position, ordered[: index + 1], candle
                    )
            if (
                index < len(ordered) - 1
                and index + 1 >= self._config.warmup_candles
                and position is None
                and pending is None
            ):
                suggested_quantity = (
                    cash * self._config.maximum_position_percent / Decimal("100") / candle.close
                )
                context = self._context_builder.build(
                    ordered[: index + 1],
                    symbol=symbol,
                    interval=interval,
                    analysis_time=candle.close_time or candle.open_time,
                    suggested_quantity=suggested_quantity,
                )
                signal = self._strategy.analyze(context)
                record = StrategyDecisionRecord(
                    record_id=f"{signal.signal_id}-RECORD",
                    analysis_time=context.created_at,
                    signal=signal,
                    context_candle_count=len(context.candles),
                    indicators=context.indicators,
                )
                if self._repository is not None:
                    self._repository.save_strategy_decision(record)
                portfolio = self._snapshot(
                    cash,
                    position,
                    candle,
                    day_start_equity,
                    entries_today,
                    orders_today,
                    closed_trades_today,
                )
                decision = self._risk_manager.evaluate(signal, portfolio, self._config)
                self._save_risk(decision)
                if decision.approved and decision.order_intent is not None:
                    pending = decision.order_intent
                    pending_signal_time = candle.open_time
                    pending_execution_index = index + self._config.latency_candles
            equity = self._equity(cash, position, candle.close)
            equity_curve.append(equity)
            exposure_curve.append(
                (position.market_value / equity * Decimal("100"))
                if position and equity
                else Decimal("0")
            )
            snapshot = self._snapshot(
                cash,
                position,
                candle,
                day_start_equity,
                entries_today,
                orders_today,
                closed_trades_today,
            )
            if self._repository is not None:
                self._repository.save_portfolio_snapshot(snapshot)
        if position is not None and self._config.force_close_at_end:
            candle = ordered[-1]
            trigger = candle.close
            exit_signal = self._exit_signal(position, trigger, candle, "FORCED_END", len(ordered))
            decision = self._risk_manager.evaluate(
                exit_signal,
                self._snapshot(
                    cash,
                    position,
                    candle,
                    day_start_equity,
                    entries_today,
                    orders_today,
                    closed_trades_today,
                ),
                self._config,
            )
            self._save_risk(decision)
            if decision.approved and decision.order_intent is not None:
                self._executor.set_reference_price(trigger)
                order = self._executor.execute(decision.order_intent)
                fill = self._fill(order, candle.close_time or candle.open_time)
                cash += order.price * order.quantity - order.fee
                if entry_candle_index is None:
                    raise RuntimeError("position lost its entry candle index")
                trades.append(
                    self._trade_record(
                        position,
                        order,
                        fill,
                        candle,
                        "FORCED_END",
                        False,
                        len(ordered) - 1,
                        entry_candle_index,
                    )
                )
                order_count += 1
                closed_trade_count += 1
                orders_today += 1
                closed_trades_today += 1
                self._save_order_fill_position(order, fill, None)
                position = None
                entry_candle_index = None
                warnings.append("open position was force-closed at the final candle close")
        final_price = ordered[-1].close
        final_capital = self._equity(cash, position, final_price)
        unrealized = self._unrealized(position, final_price)
        metrics = calculate_metrics(
            initial_capital=initial_capital,
            final_capital=final_capital,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            exposure_curve=tuple(exposure_curve),
            start_price=ordered[0].open,
            end_price=final_price,
            unrealized_pnl=unrealized,
            entry_count=entry_count,
            order_count=order_count,
            partial_exit_count=partial_exit_count,
        )
        return BacktestResult(
            report_version="2",
            strategy_version="deterministic-ema-atr-volume-v1",
            symbol=symbol,
            interval=interval,
            start_time=ordered[0].open_time,
            end_time=ordered[-1].close_time or ordered[-1].open_time,
            executed_at=self._clock(),
            candle_count=len(ordered),
            parameters=self._config.as_dict(),
            metrics=metrics,
            trades=tuple(trades),
            warnings=tuple(warnings),
        )

    def _validate_candles(self, candles: tuple[Candle, ...]) -> None:
        if not candles:
            raise ValueError("backtest requires candles")
        previous: Candle | None = None
        first = candles[0]
        for candle in candles:
            if not candle.is_closed:
                raise ValueError("backtest accepts closed candles only")
            if candle.symbol != first.symbol or candle.interval != first.interval:
                raise ValueError("backtest candles must share symbol and interval")
            if previous is not None and candle.open_time <= previous.open_time:
                raise ValueError("backtest candles must be strictly chronological")
            previous = candle

    def _snapshot(
        self,
        cash: Decimal,
        position: Position | None,
        candle: Candle,
        day_start_equity: Decimal,
        entries_today: int,
        orders_today: int,
        closed_trades_today: int,
    ) -> PortfolioSnapshot:
        positions = (position,) if position is not None else ()
        equity = self._equity(cash, position, candle.close)
        daily_loss = max(Decimal("0"), day_start_equity - equity)
        return PortfolioSnapshot(
            snapshot_id=f"snapshot-{candle.open_time.isoformat()}",
            captured_at=candle.close_time or candle.open_time,
            cash_balance=cash,
            equity=equity,
            day_start_equity=day_start_equity,
            daily_loss=daily_loss,
            entries_today=entries_today,
            orders_today=orders_today,
            closed_trades_today=closed_trades_today,
            positions=positions,
        )

    @staticmethod
    def _equity(cash: Decimal, position: Position | None, price: Decimal) -> Decimal:
        return cash + (position.quantity * price if position is not None else Decimal("0"))

    @staticmethod
    def _unrealized(position: Position | None, price: Decimal) -> Decimal:
        return (
            (price - position.average_entry_price) * position.quantity if position else Decimal("0")
        )

    def _manage_position_extensions(
        self,
        position: Position,
        cash: Decimal,
        history: tuple[Candle, ...],
        candle: Candle,
        index: int,
        trades: list[TradeRecord],
        day_start_equity: Decimal,
        entries_today: int,
        orders_today: int,
        closed_trades_today: int,
        entry_candle_index: int | None,
    ) -> tuple[Position, Decimal, int, int, int]:
        updated = position
        risk = updated.initial_risk
        if not self._config.partial_take_profit_enabled or updated.partial_taken:
            return updated, cash, 0, 0, 0
        if risk is None:
            return updated, cash, 0, 0, 0
        if updated.stop_loss is not None and candle.low <= updated.stop_loss:
            return updated, cash, 0, 0, 0
        partial_target = (
            updated.average_entry_price + risk * self._config.partial_take_profit_r_multiple
        )
        if candle.high < partial_target:
            return updated, cash, 0, 0, 0
        quantity = updated.quantity * self._config.partial_take_profit_percent / Decimal("100")
        if quantity <= 0 or quantity >= updated.quantity:
            return replace(updated, partial_taken=True), cash, 0, 0, 0
        signal = BacktestEngine._exit_signal(
            updated,
            partial_target,
            candle,
            "PARTIAL_TAKE_PROFIT",
            index,
            quantity=quantity,
        )
        decision = self._risk_manager.evaluate(
            signal,
            self._snapshot(
                cash,
                updated,
                candle,
                day_start_equity,
                entries_today,
                orders_today,
                closed_trades_today,
            ),
            self._config,
        )
        self._save_risk(decision)
        if not decision.approved or decision.order_intent is None:
            return updated, cash, 0, 0, 0
        if entry_candle_index is None:
            raise RuntimeError("position lost its entry candle index")
        self._executor.set_reference_price(partial_target)
        order = self._executor.execute(decision.order_intent)
        fill = self._fill(order, candle.close_time or candle.open_time)
        cash += order.price * order.quantity - order.fee
        allocated_fee = updated.entry_fee * order.quantity / updated.quantity
        partial_position = replace(updated, quantity=order.quantity, entry_fee=allocated_fee)
        trades.append(
            self._trade_record(
                partial_position,
                order,
                fill,
                candle,
                "PARTIAL_TAKE_PROFIT",
                False,
                index,
                entry_candle_index,
            )
        )
        remaining = replace(
            updated,
            quantity=updated.quantity - order.quantity,
            entry_fee=updated.entry_fee - allocated_fee,
            partial_taken=True,
        )
        self._save_order_fill_position(order, fill, remaining)
        return remaining, cash, 1, 1, 1

    def _update_position_protection(
        self, position: Position, history: tuple[Candle, ...], candle: Candle
    ) -> Position:
        updated = replace(position, current_price=candle.close)
        risk = updated.initial_risk
        stop_loss = updated.stop_loss
        if stop_loss is None or risk is None:
            return updated
        if len(history) >= self._config.atr_period and self._config.trailing_stop_enabled:
            trailing = (
                candle.close
                - atr(history, self._config.atr_period)
                * self._config.trailing_stop_atr_multiple
            )
            if trailing > stop_loss:
                updated = replace(updated, stop_loss=trailing)
                stop_loss = trailing
        if (
            candle.close
            >= updated.average_entry_price + risk * self._config.break_even_after_r_multiple
            and stop_loss < updated.average_entry_price
        ):
            updated = replace(updated, stop_loss=updated.average_entry_price)
        return updated

    @staticmethod
    def _exit_trigger(
        position: Position, candle: Candle
    ) -> tuple[str | None, Decimal | None, bool]:
        if position.stop_loss is None or position.take_profit is None:
            return None, None, False
        stop_hit = candle.low <= position.stop_loss
        target_hit = candle.high >= position.take_profit
        if stop_hit:
            return "STOP_LOSS", position.stop_loss, target_hit
        if target_hit:
            return "TAKE_PROFIT", position.take_profit, False
        return None, None, False

    @staticmethod
    def _exit_signal(
        position: Position,
        trigger: Decimal,
        candle: Candle,
        reason: str,
        index: int,
        *,
        quantity: Decimal | None = None,
    ) -> MarketSignal:
        return MarketSignal(
            signal_id=f"{position.position_id}-EXIT-{index}-{reason}",
            symbol=position.symbol,
            generated_at=candle.close_time or candle.open_time,
            direction=SignalDirection.SELL,
            regime=MarketRegime.UNKNOWN,
            confidence=Decimal("1"),
            entry_price=trigger,
            stop_loss=trigger,
            take_profit=trigger,
            suggested_quantity=quantity if quantity is not None else position.quantity,
            rationale=f"protective exit reason={reason}",
            analyzer_name="backtest-protective-exit",
        )

    @staticmethod
    def _fill(order: object, filled_at: datetime) -> Fill:
        from adaptive_trader.domain.models import SimulatedOrder

        if not isinstance(order, SimulatedOrder):
            raise TypeError("backtest executor must return SimulatedOrder")
        return Fill(
            fill_id=f"{order.order_id}-FILL",
            order_id=order.order_id,
            symbol=order.symbol,
            quantity=order.quantity,
            price=order.price,
            fee=order.fee,
            filled_at=filled_at,
            reference_price=order.reference_price,
            slippage_cost=order.slippage_cost,
            spread_cost=order.spread_cost,
        )

    @staticmethod
    def _trade_record(
        position: Position,
        order: object,
        fill: Fill,
        candle: Candle,
        reason: str,
        ambiguous: bool,
        index: int,
        entry_candle_index: int,
    ) -> TradeRecord:
        from adaptive_trader.domain.models import SimulatedOrder

        if not isinstance(order, SimulatedOrder):
            raise TypeError("backtest executor must return SimulatedOrder")
        gross = (order.price - position.average_entry_price) * order.quantity
        entry_fee = position.entry_fee
        net = gross - entry_fee - order.fee
        return TradeRecord(
            trade_id=f"{order.order_id}-TRADE",
            symbol=position.symbol,
            quantity=order.quantity,
            entry_time=position.opened_at,
            exit_time=candle.close_time or candle.open_time,
            entry_price=position.average_entry_price,
            exit_price=order.price,
            gross_pnl=gross,
            fees=entry_fee + order.fee,
            slippage_cost=fill.slippage_cost,
            spread_cost=fill.spread_cost,
            net_pnl=net,
            exit_reason=reason,
            intrabar_ambiguous=ambiguous,
            holding_candles=index - entry_candle_index,
        )

    def _save_risk(self, decision: object) -> None:
        from adaptive_trader.domain.models import RiskDecision

        if self._repository is not None:
            if not isinstance(decision, RiskDecision):
                raise TypeError("risk manager must return RiskDecision")
            self._repository.save_risk_decision(decision)

    def _save_order_fill_position(
        self, order: object, fill: Fill, position: Position | None
    ) -> None:
        from adaptive_trader.domain.models import SimulatedOrder

        if self._repository is None:
            return
        if not isinstance(order, SimulatedOrder):
            raise TypeError("backtest executor must return SimulatedOrder")
        self._repository.save_simulated_order(order)
        self._repository.save_fill(fill)
        if position is not None:
            self._repository.save_position(position)
