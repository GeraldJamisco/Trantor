"""Point-in-time replay views over the immutable research datasets."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime

from trantor.models import MarketObservation

from .data import ResearchData, ResearchDataError

__all__ = ["AsOfView", "HistoricalView"]


@dataclass(frozen=True, slots=True)
class AsOfView:
    decision_time: datetime
    d1: tuple[MarketObservation, ...]
    h4: tuple[MarketObservation, ...]
    m15: tuple[MarketObservation, ...]
    next_m15_bar: MarketObservation | None


class HistoricalView:
    """Read-only as-of prefix provider.

    D1 and H4 prefixes contain only bars whose close is at or before the
    decision timestamp. The strategy receives a bounded recent M15 window
    (which is sufficient for its 21-bar contiguity/RSI/volume rules) and the
    immediate next M15 bar. No index or source tuple is mutated.
    """

    def __init__(self, data: ResearchData, *, m15_lookback: int = 64) -> None:
        if not isinstance(data, ResearchData):
            raise TypeError("data must be ResearchData")
        if isinstance(m15_lookback, bool) or not isinstance(m15_lookback, int):
            raise ValueError("m15_lookback must be an integer")
        if m15_lookback < 21:
            raise ValueError("m15_lookback must be at least 21")
        self.data = data
        self.m15_lookback = m15_lookback

    def as_of(self, decision_time: datetime, *, m15_index: int | None = None) -> AsOfView:
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        d1_end = bisect_right(self.data.d1_close_times, decision_time)
        h4_end = bisect_right(self.data.h4_close_times, decision_time)
        m15_end = bisect_right(self.data.m15_close_times, decision_time)
        if m15_index is not None:
            if isinstance(m15_index, bool) or not isinstance(m15_index, int):
                raise ValueError("m15_index must be an integer")
            if m15_index < 0 or m15_index >= len(self.data.m15):
                raise ValueError("m15_index is outside the M15 dataset")
            if self.data.m15_close_times[m15_index] != decision_time:
                raise ValueError("m15_index does not close at decision_time")
            m15_end = m15_index + 1
        start = max(0, m15_end - self.m15_lookback)
        m15_window = self.data.m15[start:m15_end]
        next_m15_bar = (
            self.data.m15[m15_end] if m15_end < len(self.data.m15) else None
        )
        return AsOfView(
            decision_time=decision_time,
            d1=self.data.d1[:d1_end],
            h4=self.data.h4[:h4_end],
            m15=m15_window,
            next_m15_bar=next_m15_bar,
        )

    def decision_indices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[int, ...]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("split bounds must be timezone-aware")
        if end < start:
            raise ValueError("split end cannot precede start")
        first = bisect_left(self.data.m15_close_times, start)
        last = bisect_right(self.data.m15_close_times, end)
        return tuple(range(first, last))

    def decision_time(self, m15_index: int) -> datetime:
        if m15_index < 0 or m15_index >= len(self.data.m15_close_times):
            raise ValueError("m15_index is outside the M15 dataset")
        return self.data.m15_close_times[m15_index]

    def last_close_at_or_before(self, at: datetime) -> float | None:
        index = bisect_right(self.data.m15_close_times, at)
        if index == 0:
            return None
        return self.data.m15[index - 1].close

    def ensure_common_overlap(self) -> None:
        if not self.data.d1 or not self.data.h4 or not self.data.m15:
            raise ResearchDataError("D1, H4, and M15 datasets must all be non-empty")
        first = max(
            self.data.d1_close_times[0],
            self.data.h4_close_times[0],
            self.data.m15_close_times[0],
        )
        last = min(
            self.data.d1_close_times[-1],
            self.data.h4_close_times[-1],
            self.data.m15_close_times[-1],
        )
        if last <= first:
            raise ResearchDataError("D1/H4/M15 datasets have no common time overlap")
