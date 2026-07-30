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
    StrategyDecisionTrace,
)
from adaptive_trader.domain.protocols import MarketAnalyzer, Repository, RiskManager
from adaptive_trader.execution.backtest import BacktestOrderExecutor
from adaptive_trader.indicators import atr
from adaptive_trader.market_data.context import CandleHistoryView, MarketContextBuilder


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
        time_exit_candles: int | None = None,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._executor = executor
        self._config = config
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        if time_exit_candles is not None and time_exit_candles < 1:
            raise ValueError("time_exit_candles must be positive")
        self._time_exit_candles = time_exit_candles
        self._context_builder = MarketContextBuilder(
            minimum_candles=1,
            short_ema_period=config.short_ema_period,
            long_ema_period=config.long_ema_period,
            atr_period=config.atr_period,
            volume_period=config.volume_period,
            cache_sequential=True,
        )

    def run(
        self,
        candles: Sequence[Candle],
        *,
        evaluation_start_time: datetime | None = None,
    ) -> BacktestResult:
        ordered = tuple(candles)
        self._validate_candles(ordered)
        symbol = ordered[0].symbol
        interval = ordered[0].interval
        requested_evaluation_start = evaluation_start_time
        if evaluation_start_time is not None and (
            evaluation_start_time.tzinfo is None or evaluation_start_time.utcoffset() is None
        ):
            raise ValueError("evaluation_start_time must be timezone-aware")
        if evaluation_start_time is None:
            requested_index = 0
            evaluation_index = 0
        else:
            requested_index = next(
                (
                    index
                    for index, candle in enumerate(ordered)
                    if candle.open_time >= evaluation_start_time
                ),
                -1,
            )
            if requested_index < 0:
                raise ValueError("evaluation_start_time is after the available dataset")
            evaluation_index = max(requested_index, self._config.warmup_candles)
            if evaluation_index >= len(ordered):
                raise ValueError("evaluation period has no candles after indicator warmup")
        evaluation_candles = ordered[evaluation_index:]
        initial_capital = self._config.initial_balance
        cash = initial_capital
        position: Position | None = None
        pending = None
        pending_signal_time: datetime | None = None
        pending_execution_index: int | None = None
        entry_candle_index: int | None = None
        trades: list[TradeRecord] = []
        decision_traces: list[StrategyDecisionTrace] = []
        trace_by_intent: dict[str, int] = {}
        warnings: list[str] = [
            "BACKTEST_ONLY: no real orders were sent",
            "execution uses simulated fees, spread and slippage",
            "ambiguous intrabar policy is STOP_FIRST",
        ]
        if (
            requested_evaluation_start is not None
            and requested_evaluation_start < ordered[0].open_time
        ):
            warnings.append("EVALUATION_START_BEFORE_DATASET: using first available candle")
        if evaluation_index > requested_index:
            warnings.append(
                "WARMUP_REDUCED_EVALUATION_PERIOD: "
                f"requested={ordered[requested_index].open_time.isoformat()} "
                f"effective={ordered[evaluation_index].open_time.isoformat()}"
            )
        if len(evaluation_candles) < 2:
            warnings.append("EVALUATION_PERIOD_SHORT_FOR_INDICATORS")
        equity_curve: list[Decimal] = [initial_capital]
        exposure_curve: list[Decimal] = []
        entry_count = 0
        order_count = 0
        closed_trade_count = 0
        partial_exit_count = 0
        current_day = evaluation_candles[0].open_time.astimezone(UTC).date()
        day_start_equity = initial_capital
        entries_today = 0
        orders_today = 0
        closed_trades_today = 0
        for index, candle in enumerate(ordered):
            if index < evaluation_index:
                continue
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
                pending_intent_id = pending.intent_id
                if pending.direction is SignalDirection.BUY:
                    estimated_cost = self._executor.estimate_buy_cost(pending, candle.open)
                    if estimated_cost > cash:
                        warnings.append(
                            "entry skipped at "
                            f"{candle.open_time.isoformat()}: effective cost exceeds cash"
                        )
                        pending = None
                        pending_execution_index = None
                        self._update_trace(
                            decision_traces,
                            trace_by_intent,
                            pending_intent_id,
                            execution_status="REJECTED",
                            execution_rejection_code="INSUFFICIENT_CASH",
                        )
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
                            self._update_trace(
                                decision_traces,
                                trace_by_intent,
                                pending_intent_id,
                                execution_status="REJECTED",
                                execution_rejection_code="INSUFFICIENT_CASH",
                            )
                        elif order.price <= pending.stop_loss:
                            warnings.append(
                                "entry skipped at "
                                f"{candle.open_time.isoformat()}: gap invalidated stop"
                            )
                            pending = None
                            pending_execution_index = None
                            self._update_trace(
                                decision_traces,
                                trace_by_intent,
                                pending_intent_id,
                                execution_status="REJECTED",
                                execution_rejection_code="GAP_INVALIDATED_STOP",
                            )
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
                            self._update_trace(
                                decision_traces,
                                trace_by_intent,
                                pending_intent_id,
                                execution_status="EXECUTED",
                            )
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
                time_exit = (
                    self._time_exit_candles is not None
                    and entry_candle_index is not None
                    and index - entry_candle_index >= self._time_exit_candles
                )
                exit_reason: str | None
                trigger: Decimal | None
                ambiguous: bool
                if time_exit:
                    exit_reason, trigger, ambiguous = "TIME_EXIT", candle.close, False
                else:
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
            ready_for_analysis = (
                index >= evaluation_index
                if evaluation_start_time is not None
                else index + 1 >= self._config.warmup_candles
            )
            if (
                index < len(ordered) - 1
                and ready_for_analysis
            ):
                suggested_quantity = (
                    cash * self._config.maximum_position_percent / Decimal("100") / candle.close
                )
                context = self._context_builder.build(
                    CandleHistoryView(ordered, stop=index + 1),
                    symbol=symbol,
                    interval=interval,
                    analysis_time=candle.close_time or candle.open_time,
                    suggested_quantity=suggested_quantity,
                    validate=False,
                )
                signal = self._strategy.analyze(context)
                trace = self._trace_from_signal(
                    context=context,
                    signal=signal,
                    candle_index=index,
                    position_open=position is not None,
                    pending_order=pending is not None,
                )
                if position is not None:
                    decision_traces.append(
                        replace(
                            trace,
                            strategy_reason_code="POSITION_ALREADY_OPEN",
                            execution_status="SKIPPED",
                        )
                    )
                elif pending is not None:
                    decision_traces.append(
                        replace(
                            trace,
                            strategy_reason_code="PENDING_ORDER",
                            execution_status="PENDING",
                        )
                    )
                else:
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
                    trace = replace(
                        trace,
                        risk_approved=decision.approved,
                        risk_rejection_code=None if decision.approved else decision.reason_code,
                        execution_status="PENDING" if decision.approved else "NOT_EXECUTED",
                        execution_rejection_code=None
                        if decision.approved
                        else decision.reason_code,
                    )
                    decision_traces.append(trace)
                    trace_index = len(decision_traces) - 1
                    if decision.approved and decision.order_intent is not None:
                        pending = decision.order_intent
                        pending_signal_time = candle.open_time
                        pending_execution_index = index + self._config.latency_candles
                        trace_by_intent[pending.intent_id] = trace_index
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
        final_price = evaluation_candles[-1].close
        final_capital = self._equity(cash, position, final_price)
        unrealized = self._unrealized(position, final_price)
        metrics = calculate_metrics(
            initial_capital=initial_capital,
            final_capital=final_capital,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            exposure_curve=tuple(exposure_curve),
            start_price=evaluation_candles[0].open,
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
            start_time=evaluation_candles[0].open_time,
            end_time=evaluation_candles[-1].close_time or evaluation_candles[-1].open_time,
            executed_at=self._clock(),
            candle_count=len(evaluation_candles),
            parameters=self._config.as_dict(),
            metrics=metrics,
            trades=tuple(trades),
            warnings=tuple(warnings),
            input_candle_count=len(ordered),
            warmup_candle_count=evaluation_index,
            evaluated_candle_count=len(evaluation_candles),
            input_start_time=ordered[0].open_time,
            requested_evaluation_start_time=requested_evaluation_start,
            evaluation_start_time=evaluation_candles[0].open_time,
            evaluation_end_time=evaluation_candles[-1].close_time
            or evaluation_candles[-1].open_time,
            equity_curve=tuple(equity_curve),
            exposure_curve=tuple(exposure_curve),
            decision_traces=tuple(decision_traces),
        )

    @staticmethod
    def _trace_from_signal(
        *,
        context: object,
        signal: MarketSignal,
        candle_index: int,
        position_open: bool,
        pending_order: bool,
    ) -> StrategyDecisionTrace:
        from adaptive_trader.domain.models import MarketContext

        if not isinstance(context, MarketContext):
            raise TypeError("trace context must be a MarketContext")
        indicators = context.indicators
        short = indicators.get("ema_short")
        long = indicators.get("ema_long")
        atr_value = indicators.get("atr")
        volume_ratio = indicators.get("volume_ratio")
        close = context.latest_candle.close
        average_volume = (
            context.latest_candle.volume / volume_ratio
            if volume_ratio is not None and volume_ratio != 0
            else None
        )
        downside = signal.entry_price - signal.stop_loss
        upside = signal.take_profit - signal.entry_price
        return StrategyDecisionTrace(
            timestamp=context.created_at,
            symbol=context.symbol,
            interval=context.interval,
            candle_index=candle_index,
            close_price=close,
            regime=signal.regime,
            short_ema=short,
            long_ema=long,
            ema_distance=short - long if short is not None and long is not None else None,
            atr=atr_value,
            atr_relative=atr_value / close if atr_value is not None else None,
            volume=context.latest_candle.volume,
            average_volume=average_volume,
            volume_ratio=volume_ratio,
            risk_reward=upside / downside if downside > 0 else None,
            signal_direction=signal.direction,
            strategy_reason_code=signal.reason_code,
            risk_approved=None,
            risk_rejection_code=None,
            execution_status="NOT_APPLICABLE",
            execution_rejection_code=None,
            position_open=position_open,
            pending_order=pending_order,
        )

    @staticmethod
    def _update_trace(
        traces: list[StrategyDecisionTrace],
        trace_by_intent: dict[str, int],
        intent_id: str,
        *,
        execution_status: str,
        execution_rejection_code: str | None = None,
    ) -> None:
        trace_index = trace_by_intent.pop(intent_id, None)
        if trace_index is None:
            return
        traces[trace_index] = replace(
            traces[trace_index],
            execution_status=execution_status,
            execution_rejection_code=execution_rejection_code,
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
