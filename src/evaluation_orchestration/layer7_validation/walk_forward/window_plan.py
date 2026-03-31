"""Deterministic rolling window planner for walk-forward evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: str
    train_end: str
    select_start: str
    select_end: str
    holdout_start: str
    holdout_end: str
    forward_start: str
    forward_end: str


def _parse_date(value: str) -> date:
    raw = str(value).strip()
    if "-" in raw:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    return datetime.strptime(raw, "%Y%m%d").date()


def _fmt_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _resolve_window_cfg(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, Mapping):
        return {}
    root: Mapping[str, Any] = cfg
    if isinstance(root.get("selection"), Mapping):
        root = root["selection"]
    if isinstance(root.get("walk_forward"), Mapping):
        root = root["walk_forward"]
    if isinstance(root.get("window_plan"), Mapping):
        root = root["window_plan"]
    return dict(root)


class WalkForwardWindowPlanner:
    """Build deterministic rolling walk-forward windows."""

    def build(
        self,
        *,
        start_date: str,
        end_date: str,
        cfg: dict[str, Any],
    ) -> list[WalkForwardWindow]:
        c = _resolve_window_cfg(cfg)

        train_days = max(1, int(c.get("train_days", 5)))
        select_days = max(1, int(c.get("select_days", 1)))
        holdout_days = max(1, int(c.get("holdout_days", 1)))
        forward_days = max(1, int(c.get("forward_days", 1)))
        step_days = max(1, int(c.get("step_days", forward_days)))
        fallback_single_window = bool(c.get("fallback_single_window", True))

        start = _parse_date(start_date)
        end = _parse_date(end_date)
        if end < start:
            raise ValueError(f"end_date must be >= start_date: {start_date!r}, {end_date!r}")

        windows: list[WalkForwardWindow] = []
        cursor = start

        while cursor <= end:
            train_start = cursor
            train_end = train_start + timedelta(days=train_days - 1)

            select_start = train_end + timedelta(days=1)
            select_end = select_start + timedelta(days=select_days - 1)

            holdout_start = select_end + timedelta(days=1)
            holdout_end = holdout_start + timedelta(days=holdout_days - 1)

            forward_start = holdout_end + timedelta(days=1)
            forward_end = forward_start + timedelta(days=forward_days - 1)

            if forward_end > end:
                break

            windows.append(
                WalkForwardWindow(
                    train_start=_fmt_date(train_start),
                    train_end=_fmt_date(train_end),
                    select_start=_fmt_date(select_start),
                    select_end=_fmt_date(select_end),
                    holdout_start=_fmt_date(holdout_start),
                    holdout_end=_fmt_date(holdout_end),
                    forward_start=_fmt_date(forward_start),
                    forward_end=_fmt_date(forward_end),
                )
            )
            cursor = cursor + timedelta(days=step_days)

        if not windows and fallback_single_window:
            # Fallback for narrow ranges: evaluate one forward window spanning the full range.
            whole = WalkForwardWindow(
                train_start=_fmt_date(start),
                train_end=_fmt_date(end),
                select_start=_fmt_date(start),
                select_end=_fmt_date(end),
                holdout_start=_fmt_date(start),
                holdout_end=_fmt_date(end),
                forward_start=_fmt_date(start),
                forward_end=_fmt_date(end),
            )
            windows.append(whole)

        return windows
