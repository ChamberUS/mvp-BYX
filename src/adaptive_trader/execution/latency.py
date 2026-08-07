"""Explicit deterministic latency profiles for event-time simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class LatencyProfile(StrEnum):
    IDEALIZED = "IDEALIZED"
    FAST = "FAST"
    NORMAL = "NORMAL"
    STRESSED = "STRESSED"


@dataclass(frozen=True, slots=True)
class LatencyConfig:
    profile: LatencyProfile
    decision_latency_ms: int
    outbound_order_latency_ms: int
    exchange_processing_latency_ms: int
    inbound_ack_latency_ms: int
    cancel_outbound_latency_ms: int
    cancel_processing_latency_ms: int
    fill_notification_latency_ms: int

    def __post_init__(self) -> None:
        if min(
            self.decision_latency_ms,
            self.outbound_order_latency_ms,
            self.exchange_processing_latency_ms,
            self.inbound_ack_latency_ms,
            self.cancel_outbound_latency_ms,
            self.cancel_processing_latency_ms,
            self.fill_notification_latency_ms,
        ) < 0:
            raise ValueError("latency components must be non-negative")


PROFILES = {
    LatencyProfile.IDEALIZED: LatencyConfig(LatencyProfile.IDEALIZED, 0, 0, 0, 0, 0, 0, 0),
    LatencyProfile.FAST: LatencyConfig(LatencyProfile.FAST, 2, 3, 1, 3, 3, 1, 3),
    LatencyProfile.NORMAL: LatencyConfig(LatencyProfile.NORMAL, 8, 12, 3, 12, 12, 3, 12),
    LatencyProfile.STRESSED: LatencyConfig(
        LatencyProfile.STRESSED, 30, 75, 20, 75, 75, 20, 100
    ),
}


class LatencyModel:
    """Deterministic now; the config is ready for seeded distributions later."""

    def __init__(
        self,
        profile: LatencyProfile = LatencyProfile.NORMAL,
        *,
        config: LatencyConfig | None = None,
        seed: int = 42,
    ) -> None:
        if seed < 0:
            raise ValueError("latency seed must be non-negative")
        self.config = config or PROFILES[profile]
        self.seed = seed

    def exchange_arrival(self, decision_time: datetime) -> datetime:
        return decision_time + timedelta(
            milliseconds=(
                self.config.decision_latency_ms + self.config.outbound_order_latency_ms
            )
        )

    def acknowledgement_time(self, arrival_time: datetime) -> datetime:
        return arrival_time + timedelta(
            milliseconds=(
                self.config.exchange_processing_latency_ms
                + self.config.inbound_ack_latency_ms
            )
        )

    def cancel_effective_time(self, request_time: datetime) -> datetime:
        return request_time + timedelta(
            milliseconds=(
                self.config.cancel_outbound_latency_ms
                + self.config.cancel_processing_latency_ms
            )
        )

    def fill_notification_time(self, fill_time: datetime) -> datetime:
        return fill_time + timedelta(milliseconds=self.config.fill_notification_latency_ms)
