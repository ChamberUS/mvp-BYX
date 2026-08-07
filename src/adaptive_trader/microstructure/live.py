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
from dataclasses import dataclass
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
from adaptive_trader.microstructure.storage import (
    MicrostructureSessionSummary,
    MicrostructureSessionWriter,
)

SUPPORTED_STREAMS = frozenset({"aggTrade", "bookTicker", "depth", "markPrice"})


class PublicWebSocketProtocolError(OSError):
    pass


class PublicWebSocketConnection:
    """Read public text frames and answer server pings; no private subscription API."""

    def __init__(self, url: str, *, timeout_seconds: float = 30) -> None:
        if timeout_seconds <= 0:
            raise ValueError("WebSocket timeout must be positive")
        self.url = url
        self.timeout_seconds = timeout_seconds
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
                continue
            if opcode == 0xA:
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
        if duration_seconds <= 0:
            raise ValueError("capture duration must be positive")
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

    async def run(self) -> PublicCaptureResult:
        started_at = datetime.now(tz=UTC)
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        session_id = f"microstructure-{stamp}-{self.market_type.value.lower()}"
        writer = MicrostructureSessionWriter(
            self.output_dir,
            market_type=self.market_type,
            symbol=self.symbol,
            session_id=session_id,
            started_at=started_at,
        )
        deadline = self._monotonic() + self.duration_seconds
        complete = False
        disconnects = 0
        attempt = 0
        try:
            while self._monotonic() < deadline:
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
                        while self._monotonic() < deadline:
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
            complete = complete or self._monotonic() >= deadline
        finally:
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

    def _parse_payload(self, payload: object) -> MicrostructureEvent:
        return parse_public_event(
            payload,
            market_type=self.market_type,
            receive_wall_time=datetime.now(tz=UTC),
            receive_monotonic_ns=time.monotonic_ns(),
            expected_symbol=self.symbol,
        )

    def _stream_url(self) -> str:
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
