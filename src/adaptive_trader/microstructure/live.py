"""Small no-auth Binance public WebSocket recorder using only the standard library."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.connection import (
    ConnectionMetrics,
    ConnectionSupervisor,
    stream_capabilities,
)
from adaptive_trader.microstructure.health import StreamLivenessMonitor
from adaptive_trader.microstructure.models import (
    MicrostructureEvent,
    MicrostructureStreamType,
    OrderBookStatus,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.parsing import (
    InvalidMicrostructurePayload,
    connection_state_event,
    parse_depth_snapshot,
    parse_public_event,
)
from adaptive_trader.microstructure.provenance import (
    RecorderProvenance,
    capture_recorder_provenance,
)
from adaptive_trader.microstructure.routing import (
    FuturesConnectionPlan,
    FuturesStreamRoute,
    FuturesStreamRouter,
)
from adaptive_trader.microstructure.runtime_health import RecorderRuntimeMonitor
from adaptive_trader.microstructure.storage import (
    MicrostructureSessionSummary,
    MicrostructureSessionWriter,
    StreamSubscriptionMetadata,
)

SUPPORTED_STREAMS = frozenset({"aggTrade", "bookTicker", "depth", "markPrice"})


class PublicWebSocketProtocolError(OSError):
    pass


class PublicWebSocketConnection:
    """Read public text frames and answer server pings; no private subscription API."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30,
        heartbeat_observer: Callable[[int], None] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("WebSocket timeout must be positive")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.heartbeat_observer = heartbeat_observer
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise ValueError("only public wss URLs are supported")
        port = parsed.port or 443
        context = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                parsed.hostname,
                port,
                ssl=context,
                server_hostname=parsed.hostname,
            ),
            timeout=self.timeout_seconds,
        )
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: AdaptiveTrader/0.2 (research-only; no-auth)\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=self.timeout_seconds,
        )
        lines = response.decode("latin-1").split("\r\n")
        if not lines or " 101 " not in lines[0]:
            writer.close()
            await writer.wait_closed()
            raise PublicWebSocketProtocolError("public WebSocket upgrade was rejected")
        headers = {
            name.strip().lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in (line.split(":", 1),)
        }
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii"),
                usedforsecurity=False,
            ).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            writer.close()
            await writer.wait_closed()
            raise PublicWebSocketProtocolError("invalid public WebSocket accept header")
        self._reader = reader
        self._writer = writer

    async def receive_json(self) -> object:
        fragments = bytearray()
        while True:
            final, opcode, payload = await self._read_frame()
            if opcode == 0x8:
                raise PublicWebSocketProtocolError("public WebSocket closed by server")
            if opcode == 0x9:
                await self._send_frame(0xA, payload)
                if self.heartbeat_observer is not None:
                    self.heartbeat_observer(time.monotonic_ns())
                continue
            if opcode == 0xA:
                if self.heartbeat_observer is not None:
                    self.heartbeat_observer(time.monotonic_ns())
                continue
            if opcode not in {0x0, 0x1}:
                raise PublicWebSocketProtocolError("unsupported public WebSocket frame")
            fragments.extend(payload)
            if not final:
                continue
            try:
                return json.loads(fragments.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PublicWebSocketProtocolError(
                    "public WebSocket payload is invalid JSON"
                ) from exc

    async def close(self) -> None:
        if self._writer is None:
            return
        try:
            await self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except OSError:
            pass
        finally:
            self._reader = None
            self._writer = None

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        if self._reader is None:
            raise RuntimeError("public WebSocket is not connected")
        header = await asyncio.wait_for(
            self._reader.readexactly(2),
            timeout=self.timeout_seconds,
        )
        final = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(
                "!H",
                await self._reader.readexactly(2),
            )[0]
        elif length == 127:
            length = struct.unpack(
                "!Q",
                await self._reader.readexactly(8),
            )[0]
        if length > 16 * 1024 * 1024:
            raise PublicWebSocketProtocolError("public WebSocket frame exceeds safety limit")
        mask = await self._reader.readexactly(4) if masked else None
        payload = await self._reader.readexactly(length)
        if mask is not None:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return final, opcode, payload

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._writer is None:
            raise RuntimeError("public WebSocket is not connected")
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 65535:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._writer.write(header + mask + masked)
        await self._writer.drain()


@dataclass(frozen=True, slots=True)
class PublicCaptureResult:
    session: MicrostructureSessionSummary
    connection: ConnectionMetrics
    order_book_status: str
    public_only: bool = True
    authentication_used: bool = False
    orders_sent: bool = False


@dataclass(frozen=True, slots=True)
class _RoutedEnvelope:
    event: MicrostructureEvent
    parsing_completed_ns: int | None = None
    persistence_queued_ns: int | None = None


@dataclass(frozen=True, slots=True)
class _RoutedNotice:
    connection_id: str
    state: str
    detail: str


class PublicMicrostructureRecorder:
    def __init__(
        self,
        *,
        market_type: MarketType,
        symbol: str,
        streams: tuple[str, ...],
        output_dir: Path,
        duration_seconds: int = 60,
        maximum_reconnects: int = 3,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        if duration_seconds < 0:
            raise ValueError("capture duration must be non-negative")
        invalid = set(streams) - SUPPORTED_STREAMS
        if invalid:
            raise ValueError(f"unsupported public streams: {sorted(invalid)}")
        if market_type is MarketType.SPOT and "markPrice" in streams:
            raise ValueError("markPrice is Futures-only")
        if "depth" not in streams:
            raise ValueError("local book capture requires depth")
        self.market_type = market_type
        self.symbol = symbol.upper()
        self.streams = streams
        self.output_dir = output_dir
        self.duration_seconds = duration_seconds
        self._monotonic = monotonic_clock or time.monotonic
        self.supervisor = ConnectionSupervisor(maximum_reconnects=maximum_reconnects)
        self.book = LocalOrderBook(market_type, self.symbol)
        self._stop_requested = False
        self._disconnects = 0
        self._parser_errors = 0
        self._resync_events: list[dict[str, object]] = []

    def _provenance(self) -> RecorderProvenance:
        return capture_recorder_provenance(
            {
                "market": self.market_type.value,
                "symbol": self.symbol,
                "streams": list(self.streams),
                "depth_speed": "100ms",
                "duration_seconds": self.duration_seconds,
                "maximum_reconnects": self.supervisor.maximum_reconnects,
                "latency_profile": "CAPTURE_RUNTIME",
                "authentication": False,
            }
        )

    def request_stop(self) -> None:
        """Request a clean close; used by SIGINT/SIGTERM handlers for duration zero."""

        self._stop_requested = True

    async def run(self) -> PublicCaptureResult:
        if self.market_type is MarketType.USD_M_FUTURES:
            return await self._run_routed_futures()
        return await self._run_single_connection()

    async def _run_single_connection(self) -> PublicCaptureResult:
        started_at = datetime.now(tz=UTC)
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        session_id = f"microstructure-{stamp}-{self.market_type.value.lower()}"
        writer = MicrostructureSessionWriter(
            self.output_dir,
            market_type=self.market_type,
            symbol=self.symbol,
            session_id=session_id,
            started_at=started_at,
            subscriptions=self._subscription_metadata(),
            requested_duration_seconds=self.duration_seconds,
            provenance=self._provenance(),
        )
        deadline = (
            None
            if self.duration_seconds == 0
            else self._monotonic() + self.duration_seconds
        )
        complete = False
        disconnects = 0
        attempt = 0
        try:
            while self._capture_active(deadline):
                connection = PublicWebSocketConnection(self._stream_url())
                try:
                    await connection.connect()
                    now_ns = time.monotonic_ns()
                    self.supervisor.connected(now_ns)
                    writer.append(
                        connection_state_event(
                            market_type=self.market_type,
                            symbol=self.symbol,
                            state="CONNECTED",
                            timestamp=datetime.now(tz=UTC),
                            monotonic_ns=now_ns,
                        )
                    )
                    receive_task = await self._bootstrap_book(connection, writer)
                    attempt = 0
                    try:
                        while self._capture_active(deadline):
                            payload = await receive_task
                            receive_task = asyncio.create_task(connection.receive_json())
                            event = self._parse_payload(payload)
                            writer.append(event)
                            if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                                result = self.book.apply_update(event)
                                if result.status is OrderBookStatus.INVALID:
                                    self.supervisor.sequence_gap_observed()
                                    self.supervisor.resync_observed()
                                    self.book.begin_resync()
                                    snapshot = await self._snapshot_event()
                                    writer.append(snapshot)
                                    self.supervisor.snapshot_observed()
                                    self.book.apply_snapshot(snapshot)
                    finally:
                        receive_task.cancel()
                        await asyncio.gather(receive_task, return_exceptions=True)
                    complete = True
                except (
                    OSError,
                    TimeoutError,
                    InvalidMicrostructurePayload,
                    httpx.HTTPError,
                    ValueError,
                ):
                    disconnects += 1
                    disconnected_ns = time.monotonic_ns()
                    self.supervisor.disconnected(disconnected_ns)
                    writer.append(
                        connection_state_event(
                            market_type=self.market_type,
                            symbol=self.symbol,
                            state="DISCONNECTED",
                            timestamp=datetime.now(tz=UTC),
                            monotonic_ns=disconnected_ns,
                        )
                    )
                    attempt += 1
                    if attempt > self.supervisor.maximum_reconnects:
                        break
                    delay_ms = self.supervisor.reconnect_delay_ms(attempt)
                    await asyncio.sleep(delay_ms / 1000)
                finally:
                    await connection.close()
            complete = complete or self._stop_requested or (
                deadline is not None and self._monotonic() >= deadline
            )
        finally:
            writer.set_capture_metadata(
                parser_errors=0,
                liveness_summary={},
                order_book_status=self.book.status.value,
            )
            summary = writer.close(
                complete=complete,
                gaps=self.supervisor.metrics.sequence_gap_count,
                disconnects=disconnects,
                resyncs=self.supervisor.metrics.resync_count,
            )
        return PublicCaptureResult(
            session=summary,
            connection=self.supervisor.metrics,
            order_book_status=self.book.status.value,
        )

    async def _run_routed_futures(self) -> PublicCaptureResult:
        router = FuturesStreamRouter()
        plans = router.plans(self.symbol, self.streams)
        for plan in plans:
            router.validate_url(plan)
        started_at = datetime.now(tz=UTC)
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        session_id = f"microstructure-{stamp}-{self.market_type.value.lower()}"
        writer = MicrostructureSessionWriter(
            self.output_dir,
            market_type=self.market_type,
            symbol=self.symbol,
            session_id=session_id,
            started_at=started_at,
            subscriptions=self._subscription_metadata(plans),
            requested_duration_seconds=self.duration_seconds,
            provenance=self._provenance(),
        )
        liveness = StreamLivenessMonitor(
            tuple(
                (stream.requested_stream, plan.connection_id)
                for plan in plans
                for stream in plan.streams
            )
        )
        runtime = RecorderRuntimeMonitor()
        stream_lookup = {
            (plan.connection_id, self._base_stream(stream.requested_stream)):
            stream.requested_stream
            for plan in plans
            for stream in plan.streams
        }
        deadline = (
            None
            if self.duration_seconds == 0
            else self._monotonic() + self.duration_seconds
        )
        queue: asyncio.Queue[_RoutedEnvelope | _RoutedNotice] = asyncio.Queue(
            maxsize=runtime.queue_capacity
        )
        workers = tuple(
            asyncio.create_task(
                self._route_worker(plan, queue, deadline, liveness, runtime)
            )
            for plan in plans
        )
        public_connection_id = next(
            plan.connection_id
            for plan in plans
            if plan.route is FuturesStreamRoute.PUBLIC
        )
        snapshot_task: asyncio.Task[MicrostructureEvent] | None = None
        snapshot_sequence = 0
        snapshot_failures = 0
        complete = False
        try:
            while self._capture_active(deadline) or not queue.empty():
                loop_now_ns = time.monotonic_ns()
                runtime.observe_loop(loop_now_ns)
                runtime.observe_queue(queue.qsize())
                liveness.evaluate(loop_now_ns, datetime.now(tz=UTC))
                batch: list[_RoutedEnvelope | _RoutedNotice] = []
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=0.2))
                    while not queue.empty():
                        batch.append(queue.get_nowait())
                except TimeoutError:
                    pass
                envelopes = sorted(
                    (item for item in batch if isinstance(item, _RoutedEnvelope)),
                    key=lambda item: self._merge_order(item.event),
                )
                for envelope in envelopes:
                    event = envelope.event
                    if event.stream_type is MicrostructureStreamType.CONNECTION_STATE:
                        writer.append(event)
                        if event.connection_state == "CONNECTED":
                            liveness.connected(event.connection_id, event.receive_monotonic_ns)
                            if (
                                event.connection_id == public_connection_id
                                and snapshot_task is None
                            ):
                                snapshot_task = asyncio.create_task(self._snapshot_event())
                        elif event.connection_state == "DISCONNECTED":
                            liveness.connection_restarted(
                                event.connection_id,
                                event.receive_monotonic_ns,
                            )
                        continue
                    processing_started_ns = time.monotonic_ns()
                    base_stream = self._event_stream_name(event)
                    requested_stream = stream_lookup[(event.connection_id, base_stream)]
                    previous_update_id = self.book.update_id
                    gap_count_before = self.supervisor.metrics.sequence_gap_count
                    needs_snapshot = self._apply_routed_depth(event)
                    book_update_completed_ns = time.monotonic_ns()
                    sequence_continuous = (
                        event.sequence_previous == previous_update_id
                        if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE
                        and previous_update_id is not None
                        else None
                    )
                    liveness.observed(
                        requested_stream,
                        connection_id=event.connection_id,
                        connection_sequence=event.connection_sequence,
                        now_ns=event.receive_monotonic_ns,
                        now_wall=event.receive_wall_time,
                        sequence_continuous=sequence_continuous,
                        caused_gap=(
                            self.supervisor.metrics.sequence_gap_count
                            > gap_count_before
                        ),
                        caused_resync=needs_snapshot,
                        caused_book_invalid=needs_snapshot,
                        local_processing_delay=runtime.has_processing_delay(
                            event.receive_monotonic_ns,
                            processing_started_ns,
                        ),
                        queue_depth=queue.qsize(),
                    )
                    if needs_snapshot:
                        if snapshot_task is not None:
                            snapshot_task.cancel()
                            await asyncio.gather(snapshot_task, return_exceptions=True)
                        snapshot_task = asyncio.create_task(self._snapshot_event())
                    persistence_started_ns = time.monotonic_ns()
                    writer.append(event)
                    persistence_completed_ns = time.monotonic_ns()
                    runtime.processed(
                        receive_monotonic_ns=event.receive_monotonic_ns,
                        processing_started_ns=processing_started_ns,
                        book_update_completed_ns=book_update_completed_ns,
                        persistence_started_ns=persistence_started_ns,
                        persistence_completed_ns=persistence_completed_ns,
                        queue_depth=queue.qsize(),
                    )
                for notice in (
                    item for item in batch if isinstance(item, _RoutedNotice)
                ):
                    if notice.state == "PARSER_ERROR":
                        self._parser_errors += 1
                if snapshot_task is not None and snapshot_task.done():
                    try:
                        snapshot = snapshot_task.result()
                    except (OSError, TimeoutError, httpx.HTTPError, ValueError):
                        snapshot_failures += 1
                        snapshot_task = None
                        if snapshot_failures <= self.supervisor.maximum_reconnects:
                            snapshot_task = asyncio.create_task(self._snapshot_event())
                    else:
                        snapshot_sequence += 1
                        snapshot = replace(
                            snapshot,
                            connection_id="futures-rest-snapshot-1",
                            connection_sequence=snapshot_sequence,
                        )
                        writer.append(snapshot)
                        self.supervisor.snapshot_observed()
                        result = self.book.apply_snapshot(snapshot)
                        snapshot_task = None
                        if result.status is OrderBookStatus.INVALID:
                            self._record_resync(result.gap_classification, snapshot)
                            self.supervisor.resync_observed()
                            self.book.begin_resync()
                            snapshot_task = asyncio.create_task(self._snapshot_event())
                if all(worker.done() for worker in workers) and queue.empty():
                    break
            complete = self._stop_requested or (
                deadline is not None and self._monotonic() >= deadline
            )
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            if snapshot_task is not None:
                snapshot_task.cancel()
                await asyncio.gather(snapshot_task, return_exceptions=True)
            ended_ns = time.monotonic_ns()
            runtime.observe_queue(queue.qsize())
            liveness.evaluate(ended_ns, datetime.now(tz=UTC))
            liveness.finalize(ended_ns, datetime.now(tz=UTC))
            writer.set_capture_metadata(
                parser_errors=self._parser_errors,
                liveness_summary=liveness.summary(),
                resync_events=tuple(self._resync_events),
                liveness_config=liveness.config_summary(),
                liveness_incidents=tuple(
                    incident.as_dict() for incident in liveness.incidents
                ),
                runtime_health=runtime.summary_dict(),
                processing_latency=runtime.latency_summary(),
                order_book_status=self.book.status.value,
            )
            summary = writer.close(
                complete=complete,
                gaps=self.supervisor.metrics.sequence_gap_count,
                disconnects=self._disconnects,
                resyncs=self.supervisor.metrics.resync_count,
            )
        return PublicCaptureResult(
            session=summary,
            connection=self.supervisor.metrics,
            order_book_status=self.book.status.value,
        )

    async def _route_worker(
        self,
        plan: FuturesConnectionPlan,
        queue: asyncio.Queue[_RoutedEnvelope | _RoutedNotice],
        deadline: float | None,
        liveness: StreamLivenessMonitor,
        runtime: RecorderRuntimeMonitor,
    ) -> None:
        sequence = 0
        attempt = 0
        while self._capture_active(deadline):
            connection = PublicWebSocketConnection(
                plan.url,
                heartbeat_observer=lambda now_ns: liveness.heartbeat(
                    plan.connection_id, now_ns
                ),
            )
            try:
                await connection.connect()
                connected_ns = time.monotonic_ns()
                self.supervisor.connected(connected_ns)
                sequence += 1
                await queue.put(
                    _RoutedEnvelope(
                        connection_state_event(
                            market_type=self.market_type,
                            symbol=self.symbol,
                            state="CONNECTED",
                            timestamp=datetime.now(tz=UTC),
                            monotonic_ns=connected_ns,
                            connection_id=plan.connection_id,
                            connection_sequence=sequence,
                        )
                    )
                )
                attempt = 0
                while self._capture_active(deadline):
                    payload = await connection.receive_json()
                    receive_wall_time = datetime.now(tz=UTC)
                    receive_monotonic_ns = time.monotonic_ns()
                    sequence += 1
                    event = self._parse_payload(
                        payload,
                        connection_id=plan.connection_id,
                        connection_sequence=sequence,
                        receive_wall_time=receive_wall_time,
                        receive_monotonic_ns=receive_monotonic_ns,
                    )
                    parsing_completed_ns = time.monotonic_ns()
                    persistence_queued_ns = time.monotonic_ns()
                    runtime.received(
                        receive_monotonic_ns=receive_monotonic_ns,
                        parsing_completed_ns=parsing_completed_ns,
                        persistence_queued_ns=persistence_queued_ns,
                        queue_depth=queue.qsize(),
                    )
                    try:
                        queue.put_nowait(
                            _RoutedEnvelope(
                                event,
                                parsing_completed_ns,
                                persistence_queued_ns,
                            )
                        )
                    except asyncio.QueueFull:
                        runtime.dropped()
                    runtime.observe_queue(queue.qsize())
            except asyncio.CancelledError:
                raise
            except (
                OSError,
                TimeoutError,
                InvalidMicrostructurePayload,
                httpx.HTTPError,
                ValueError,
            ) as exc:
                self._disconnects += 1
                disconnected_ns = time.monotonic_ns()
                self.supervisor.disconnected(disconnected_ns)
                sequence += 1
                await queue.put(
                    _RoutedEnvelope(
                        connection_state_event(
                            market_type=self.market_type,
                            symbol=self.symbol,
                            state="DISCONNECTED",
                            timestamp=datetime.now(tz=UTC),
                            monotonic_ns=disconnected_ns,
                            connection_id=plan.connection_id,
                            connection_sequence=sequence,
                        )
                    )
                )
                state = (
                    "PARSER_ERROR"
                    if isinstance(exc, InvalidMicrostructurePayload)
                    else "CONNECTION_RESTART"
                )
                await queue.put(_RoutedNotice(plan.connection_id, state, str(exc)))
                attempt += 1
                if attempt > self.supervisor.maximum_reconnects:
                    await queue.put(
                        _RoutedNotice(plan.connection_id, "FAILED", "reconnects exhausted")
                    )
                    return
                delay_ms = self.supervisor.reconnect_delay_ms(attempt)
                await asyncio.sleep(delay_ms / 1000)
            finally:
                await connection.close()

    def _apply_routed_depth(self, event: MicrostructureEvent) -> bool:
        if event.stream_type is not MicrostructureStreamType.DEPTH_UPDATE:
            return False
        result = (
            self.book.apply_update(event)
            if self.book.synchronized
            else self.book.buffer_update(event)
        )
        if result.status is not OrderBookStatus.INVALID:
            return False
        self._record_resync(result.gap_classification, event)
        if result.gap_classification is not None:
            if result.gap_classification.value == "REAL_SEQUENCE_GAP":
                self.supervisor.sequence_gap_observed()
        self.supervisor.resync_observed()
        self.book.begin_resync()
        return True

    def _record_resync(
        self,
        classification: object,
        event: MicrostructureEvent,
    ) -> None:
        value = getattr(classification, "value", None)
        self._resync_events.append(
            {
                "classification": value or "UNCLASSIFIED_BOOK_INVALIDATION",
                "event_id": event.event_id,
                "connection_id": event.connection_id,
                "connection_sequence": event.connection_sequence,
                "sequence_first": event.sequence_first,
                "sequence_last": event.sequence_last,
                "sequence_previous": event.sequence_previous,
                "observed_at": event.receive_wall_time.isoformat(),
            }
        )

    def _subscription_metadata(
        self,
        plans: tuple[FuturesConnectionPlan, ...] | None = None,
    ) -> tuple[StreamSubscriptionMetadata, ...]:
        if plans is not None:
            return tuple(
                StreamSubscriptionMetadata(
                    requested_stream=stream.requested_stream,
                    canonical_stream=stream.stream_name,
                    route=plan.route.value,
                    connection_id=plan.connection_id,
                    url=plan.url,
                )
                for plan in plans
                for stream in plan.streams
            )
        capabilities = stream_capabilities(self.market_type, self.symbol)
        mapping = {
            "aggTrade": capabilities.aggregate_trade_stream,
            "bookTicker": capabilities.book_ticker_stream,
            "depth": capabilities.diff_depth_stream,
        }
        url = self._stream_url()
        return tuple(
            StreamSubscriptionMetadata(
                requested_stream=item,
                canonical_stream=mapping[item],
                route="SPOT_PUBLIC",
                connection_id="legacy-public-1",
                url=url,
            )
            for item in self.streams
        )

    @staticmethod
    def _event_stream_name(event: MicrostructureEvent) -> str:
        mapping = {
            MicrostructureStreamType.AGG_TRADE: "aggTrade",
            MicrostructureStreamType.BOOK_TICKER: "bookTicker",
            MicrostructureStreamType.DEPTH_UPDATE: "depth",
            MicrostructureStreamType.MARK_PRICE: "markPrice",
        }
        try:
            return mapping[event.stream_type]
        except KeyError as exc:
            raise ValueError("event is not a subscribed public stream") from exc

    @staticmethod
    def _base_stream(requested_stream: str) -> str:
        return requested_stream.split("@", 1)[0]

    @staticmethod
    def _merge_order(
        event: MicrostructureEvent,
    ) -> tuple[datetime, datetime, str, int, int, str]:
        transaction_time = event.exchange_transaction_time or event.exchange_event_time
        return (
            event.exchange_event_time,
            transaction_time,
            event.connection_id,
            event.connection_sequence,
            event.receive_monotonic_ns,
            event.event_id,
        )

    def _capture_active(self, deadline: float | None) -> bool:
        return not self._stop_requested and (
            deadline is None or self._monotonic() < deadline
        )

    async def _bootstrap_book(
        self,
        connection: PublicWebSocketConnection,
        writer: MicrostructureSessionWriter,
    ) -> asyncio.Task[object]:
        """Buffer stream updates in application memory while REST snapshot is in flight."""

        snapshot_task = asyncio.create_task(self._snapshot_event())
        receive_task = asyncio.create_task(connection.receive_json())
        try:
            while not snapshot_task.done():
                done, _ = await asyncio.wait(
                    (snapshot_task, receive_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receive_task in done:
                    event = self._parse_payload(receive_task.result())
                    writer.append(event)
                    if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                        self.book.buffer_update(event)
                    receive_task = asyncio.create_task(connection.receive_json())
            snapshot = snapshot_task.result()
            writer.append(snapshot)
            self.supervisor.snapshot_observed()
            result = self.book.apply_snapshot(snapshot)
            if result.status is OrderBookStatus.INVALID:
                raise ValueError("public order book bootstrap failed sequence validation")
            return receive_task
        except (
            OSError,
            TimeoutError,
            InvalidMicrostructurePayload,
            httpx.HTTPError,
            ValueError,
        ):
            snapshot_task.cancel()
            receive_task.cancel()
            await asyncio.gather(snapshot_task, receive_task, return_exceptions=True)
            raise

    def _parse_payload(
        self,
        payload: object,
        *,
        connection_id: str = "legacy-public-1",
        connection_sequence: int = 0,
        receive_wall_time: datetime | None = None,
        receive_monotonic_ns: int | None = None,
    ) -> MicrostructureEvent:
        return parse_public_event(
            payload,
            market_type=self.market_type,
            receive_wall_time=receive_wall_time or datetime.now(tz=UTC),
            receive_monotonic_ns=(
                receive_monotonic_ns
                if receive_monotonic_ns is not None
                else time.monotonic_ns()
            ),
            expected_symbol=self.symbol,
            connection_id=connection_id,
            connection_sequence=connection_sequence,
        )

    def _stream_url(self) -> str:
        if self.market_type is MarketType.USD_M_FUTURES:
            plans = FuturesStreamRouter().plans(self.symbol, self.streams)
            if len(plans) != 1:
                raise ValueError("Futures streams require separate PUBLIC and MARKET URLs")
            return plans[0].url
        capabilities = stream_capabilities(self.market_type, self.symbol)
        mapping = {
            "aggTrade": capabilities.aggregate_trade_stream,
            "bookTicker": capabilities.book_ticker_stream,
            "depth": capabilities.diff_depth_stream,
            "markPrice": capabilities.mark_price_stream,
        }
        names = tuple(mapping[item] for item in self.streams)
        if any(name is None for name in names):
            raise ValueError("requested stream is not available for this market")
        return f"{capabilities.websocket_base_url}?streams={'/'.join(str(name) for name in names)}"

    async def _snapshot_event(self) -> MicrostructureEvent:
        capabilities = stream_capabilities(self.market_type, self.symbol)
        params = urlencode({"symbol": self.symbol, "limit": 1000})
        url = f"{capabilities.depth_snapshot_url}{capabilities.depth_snapshot_path}?{params}"
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "AdaptiveTrader/0.2 (research-only; no-auth)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        return parse_depth_snapshot(
            payload,
            market_type=self.market_type,
            symbol=self.symbol,
            receive_wall_time=datetime.now(tz=UTC),
            receive_monotonic_ns=time.monotonic_ns(),
        )
