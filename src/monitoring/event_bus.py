"""
monitoring/event_bus.py
-----------------------
Thread-safe append-only event collector for the monitoring module.

Usage
-----
bus = EventBus(verbose=False)
bus.emit(some_event)
df  = bus.to_dataframe(FillEvent)
bus.summary()   # {'FillEvent': 3, 'TickStartEvent': 12, ...}
"""
from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any, Type, TypeVar

import pandas as pd

T = TypeVar("T")


class EventBus:
    """
    Append-only event bus for backtest monitoring.

    Verbosity modes
    ---------------
    verbose=False (default)
        QueueTickEvent is collected **only** for order IDs listed in
        filter_order_ids.  All other event types are always collected.
    verbose=True
        Every event of every type is collected unconditionally.
    """

    def __init__(
        self,
        verbose: bool = False,
        filter_order_ids: set[str] | None = None,
        rng_seed: int | None = None,
    ) -> None:
        self._verbose = verbose
        self._filter_order_ids: set[str] = filter_order_ids or set()
        self._events: list[Any] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def emit(self, event: Any) -> None:
        """Append event to the internal list (thread-safe)."""
        from monitoring.events import QueueTickEvent

        if not self._verbose and isinstance(event, QueueTickEvent):
            if self._filter_order_ids and event.child_id not in self._filter_order_ids:
                return

        with self._lock:
            self._events.append(event)

    def query(self, event_type: type, **filters) -> list:
        """Return all events of event_type matching keyword filters.

        Example
        -------
        bus.query(FillEvent, symbol="005930", is_maker=True)
        """
        with self._lock:
            results = [e for e in self._events if isinstance(e, event_type)]

        for key, value in filters.items():
            results = [e for e in results if getattr(e, key, None) == value]

        return results

    def to_dataframe(self, event_type: type) -> pd.DataFrame:
        """Convert all events of event_type to a DataFrame."""
        events = self.query(event_type)
        if not events:
            return pd.DataFrame()
        return pd.DataFrame([asdict(e) for e in events])

    def clear(self) -> None:
        """Remove all stored events."""
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def summary(self) -> dict[str, int]:
        """Return event count per event type name."""
        with self._lock:
            counts: dict[str, int] = {}
            for event in self._events:
                name = type(event).__name__
                counts[name] = counts.get(name, 0) + 1
        return counts
