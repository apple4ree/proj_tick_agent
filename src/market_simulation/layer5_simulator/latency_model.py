"""
latency_model.py
----------------
지연 simulation for Layer 5.

Models the round-trip latency from strategy signal to exchange acknowledgment,
and venue lifecycle delays.

Strategy-side observation/decision delays (`market_data_delay_ms`, `decision_compute_ms`)
are intentionally NOT modeled here; they are applied in `PipelineRunner` as
decision-path stale-state lookup semantics to avoid double-counting with venue latency.

LatencyProfile holds the baseline timing constants.
LatencyModel samples stochastic latency values and applies observation delay.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from data.layer0_data.market_state import MarketState


@dataclass
class LatencyProfile:
    """
    Timing constants (in milliseconds) for a specific exchange / connectivity.

    속성
    ----------
    order_submit_ms : float
        One-way latency from strategy to exchange gateway for order submission.
    order_ack_ms : float
        Time from submission to acknowledgment (exchange processing + round-trip).
    cancel_ms : float
        One-way latency for cancel requests.
    market_data_delay_ms : float
        Compatibility-only field for legacy callers.
        PipelineRunner uses top-level `market_data_delay_ms` in BacktestConfig
        as the canonical observation-lag source.
    """
    order_submit_ms: float = 0.5
    order_ack_ms: float = 1.0
    cancel_ms: float = 0.3
    market_data_delay_ms: float = 0.2

    @classmethod
    def zero(cls) -> LatencyProfile:
        """Instant-fill (zero latency) profile for unit testing."""
        return cls(0.0, 0.0, 0.0, 0.0)

    @classmethod
    def colocation(cls) -> LatencyProfile:
        """Typical co-location profile on KRX."""
        return cls(0.05, 0.15, 0.05, 0.05)

    @classmethod
    def retail(cls) -> LatencyProfile:
        """Typical retail-broker API latency."""
        return cls(5.0, 15.0, 3.0, 2.0)


class LatencyModel:
    """
    Stochastic latency sampler and observation-delay applier.

    For each latency component, samples from a normal distribution centred on
    the LatencyProfile value with std = jitter_std_ms (when add_jitter=True).
    Negative samples are clamped to 0.

    매개변수
    ----------
    profile : LatencyProfile | None
        Baseline latency constants.  Defaults to LatencyProfile() (retail-ish).
    add_jitter : bool
        Whether to add Gaussian noise around the profile values.
    jitter_std_ms : float
        Standard deviation of the jitter noise (ms).
    seed : int | None
        RNG seed for reproducible sampling.
    """

    def __init__(
        self,
        profile: Optional[LatencyProfile] = None,
        add_jitter: bool = True,
        jitter_std_ms: float = 0.1,
        seed: Optional[int] = None,
    ) -> None:
        self.profile = profile if profile is not None else LatencyProfile()
        self.add_jitter = add_jitter
        self.jitter_std_ms = jitter_std_ms
        self.rng: np.random.Generator = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # Samplers
    # ------------------------------------------------------------------

    def _sample(self, base_ms: float) -> float:
        """Sample a latency value with optional jitter."""
        if not self.add_jitter or self.jitter_std_ms == 0.0:
            return max(0.0, base_ms)
        noise = float(self.rng.normal(0.0, self.jitter_std_ms))
        return max(0.0, base_ms + noise)

    def sample_submit_latency(self) -> float:
        """Sample order-submission latency (ms)."""
        return self._sample(self.profile.order_submit_ms)

    def sample_ack_latency(self) -> float:
        """Sample exchange-acknowledgment latency (ms)."""
        return self._sample(self.profile.order_ack_ms)

    def sample_submit_and_ack_latency(self) -> tuple[float, float]:
        """Sample one submit/ack pair for a single order lifecycle."""
        submit_ms = self.sample_submit_latency()
        ack_ms = self.sample_ack_latency()
        return submit_ms, ack_ms

    def sample_cancel_latency(self) -> float:
        """Sample cancel-request latency (ms)."""
        return self._sample(self.profile.cancel_ms)

    def sample_data_delay(self) -> float:
        """Sample profile market-data delay (ms, compatibility only)."""
        return self._sample(self.profile.market_data_delay_ms)

    def total_round_trip_ms(self) -> float:
        """
        Sample total round-trip time: submit + ack.
        Does *not* include strategy-side compute delay (`decision_compute_ms`).
        """
        submit_ms, ack_ms = self.sample_submit_and_ack_latency()
        return submit_ms + ack_ms

    # ------------------------------------------------------------------
    # Observation delay
    # ------------------------------------------------------------------

    def apply_observation_delay(
        self,
        state: MarketState,
        delay_ms: float,
    ) -> MarketState:
        """
        Return a shallow copy of `state` with the timestamp shifted backward
        by `delay_ms` milliseconds to simulate stale market-data observation.

        The LOB snapshot and all features are unchanged; only the timestamp
        is modified to reflect when the data was actually *captured*.
        """
        if delay_ms == 0.0:
            return state

        delayed_ts = state.timestamp - pd.Timedelta(milliseconds=delay_ms)

        # Shallow copy the MarketState dataclass
        stale_state = copy.copy(state)
        object.__setattr__(stale_state, "timestamp", delayed_ts)
        return stale_state
