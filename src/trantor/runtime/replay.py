"""Historical replay through the complete runtime.

:class:`ReplaySource` serves the validated Phase B datasets as if they were a
live MT5 terminal driven by a simulated clock:

- ``rates`` returns completed bars plus the forming bar, and the forming bar
  reveals **only its open** (high = low = close = open, no volume);
- ``tick`` returns the forming bar's open as both bid and ask (no spread is
  invented; replay quotes are labelled);
- broker metadata calls return nothing, so economics stay unverified.

:func:`run_runtime_replay` steps the real :class:`TrantorRuntime` once per M15
bar open. Dataset timestamps are used with a zero offset, i.e. the replay is in
``dataset_time`` exactly like the Phase E backtester.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import ExecutionMode, Settings, TradingMode
from ..models import MarketObservation
from ..research.backtest.data import ResearchData
from ..strategy.xauusd_p import XAUUSDPMultiTimeframeStrategy
from .candles import TIMEFRAME_DURATION, RawCandle
from .mt5_gateway import Tick

__all__ = ["ReplaySource", "SimClock", "replay_settings", "run_runtime_replay"]

UTC = timezone.utc


@dataclass
class SimClock:
    now: datetime
    mono: float = 0.0

    def host(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def set(self, at: datetime) -> None:
        if at < self.now:
            raise ValueError("simulated time cannot go backwards")
        self.mono += (at - self.now).total_seconds()
        self.now = at


def _raw(bar: MarketObservation, *, forming: bool) -> RawCandle:
    if forming:
        return RawCandle(
            time=int(bar.observed_at.timestamp()),
            open=bar.open,
            high=bar.open,
            low=bar.open,
            close=bar.open,
            tick_volume=None,
        )
    return RawCandle(
        time=int(bar.observed_at.timestamp()),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        tick_volume=bar.volume,
    )


@dataclass
class ReplaySource:
    data: ResearchData
    clock: SimClock
    symbol: str = "XAUUSD-P"
    _last_tick: Tick | None = None
    calls: dict[str, int] = field(default_factory=dict)

    def _series(self, timeframe: str) -> tuple[tuple[MarketObservation, ...], tuple[datetime, ...]]:
        if timeframe == "1d":
            return self.data.d1, self.data.d1_close_times
        if timeframe == "4h":
            return self.data.h4, self.data.h4_close_times
        if timeframe == "15m":
            return self.data.m15, self.data.m15_close_times
        raise ValueError(timeframe)

    def health(self) -> dict[str, Any]:
        return {"connected": True, "replay": True}

    def tick(self) -> Tick | None:
        bars, closes = self._series("15m")
        index = bisect_right(closes, self.clock.now)
        if index < len(bars) and bars[index].observed_at <= self.clock.now:
            bar = bars[index]
            self._last_tick = Tick(raw_time=self.clock.now, bid=bar.open, ask=bar.open, last=None)
        return self._last_tick

    def rates(self, timeframe: str, count: int) -> list[RawCandle] | None:
        self.calls[timeframe] = self.calls.get(timeframe, 0) + 1
        bars, closes = self._series(timeframe)
        completed_end = bisect_right(closes, self.clock.now)
        rows = [_raw(bar, forming=False) for bar in bars[max(0, completed_end - count + 1) : completed_end]]
        if completed_end < len(bars) and bars[completed_end].observed_at <= self.clock.now:
            rows.append(_raw(bars[completed_end], forming=True))
        return rows

    def symbol_info(self) -> None:
        return None

    def account(self) -> None:
        return None

    def calc_profit(self, *args: Any) -> None:
        return None

    def calc_margin(self, *args: Any) -> None:
        return None

    def deal_commissions(self, *args: Any) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        return None


def replay_settings(mode: ExecutionMode = ExecutionMode.RESEARCH) -> Settings:
    return Settings(
        schema_version=1,
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        broker_adapter="paper",
        max_risk_per_trade_fraction=0.01,
        max_concurrent_positions=2,
        max_daily_loss_fraction=0.03,
        market_data_provider="disabled",
        execution_mode=mode,
    )


def run_runtime_replay(
    data: ResearchData,
    *,
    start: datetime,
    end: datetime,
    state_dir: Path,
    mode: ExecutionMode = ExecutionMode.RESEARCH,
    strategy: XAUUSDPMultiTimeframeStrategy | None = None,
    equity_model: str = "neutral",
    step_offset: timedelta = timedelta(seconds=1),
    full_history: bool = True,
    restart_every: int | None = None,
) -> dict[str, Any]:
    """Step the full runtime once per M15 bar open in ``[start, end]``.

    ``restart_every`` shuts the runtime down and starts a new one from the same
    state directory every N bars, exercising restart recovery mid-replay.
    """

    from .service import RuntimeConfig, TrantorRuntime

    first_open = data.m15[bisect_right(data.m15_close_times, start)].observed_at
    clock = SimClock(now=first_open - timedelta(seconds=5))
    source = ReplaySource(data, clock)
    counts = (
        {"1d": len(data.d1) + 1, "4h": len(data.h4) + 1, "15m": 200}
        if full_history
        else {"1d": 2000, "4h": 600, "15m": 200}
    )
    config = RuntimeConfig(
        settings=replay_settings(mode),
        state_dir=state_dir,
        poll_interval=0.0,
        bar_refresh_interval=0.0,
        status_interval=3600.0,
        verification_interval=None,
        calendar_refresh_interval=3600.0,
        bar_counts=counts,
        equity_model=equity_model,
        starting_equity=1.0 if equity_model == "neutral" else 10_000.0,
        slow_refresh=timedelta(days=7),
    )

    def build() -> TrantorRuntime:
        runtime = TrantorRuntime(
            config=config,
            source=source,
            host_clock=clock.host,
            monotonic=clock.monotonic,
            strategy=strategy or XAUUSDPMultiTimeframeStrategy(),
        )
        runtime.start()
        # Replay clock: dataset time with a zero offset. Seed three agreeing
        # samples so the replay starts with a measured (replay) clock.
        for seconds in (3, 2, 1):
            at = clock.now - timedelta(seconds=seconds)
            runtime.timebase.observe(at, at)
        return runtime

    runtime = build()
    restarts = 0
    steps = 0
    try:
        bars, closes = data.m15, data.m15_close_times
        index = bisect_right(closes, start)
        while index < len(bars) and bars[index].observed_at <= end:
            clock.set(bars[index].observed_at + step_offset)
            runtime.step()
            steps += 1
            index += 1
            if restart_every and steps % restart_every == 0:
                runtime.shutdown("replay restart test")
                restarts += 1
                runtime = build()
        stats = dict(runtime.stats)
        status = runtime.status()
    finally:
        runtime.shutdown("replay finished")
    return {"steps": steps, "restarts": restarts, "stats": stats, "status": status, "source_calls": source.calls}


def load_runtime_decisions(db_path: Path) -> list[dict[str, Any]]:
    from .store import RuntimeStore

    store = RuntimeStore(db_path, read_only=True)
    try:
        rows = store.recent_decisions(500)
        offset = 500
        result = list(rows)
        while len(rows) == 500:
            rows = [dict(r) for r in store._query(  # noqa: SLF001 - test/report helper
                "SELECT * FROM decisions ORDER BY decision_time DESC LIMIT 500 OFFSET ?", (offset,)
            )]
            result.extend(rows)
            offset += 500
        return sorted(result, key=lambda row: row["decision_time"])
    finally:
        store.close()


def decision_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_time": row["decision_time"],
        "status": row["status"],
        "rejection_codes": json.loads(row["rejection_codes"]),
        "record_hash": row["record_hash"],
        "outcome": row["outcome"],
    }
