"""Broker clock, completed-candle identification and live feed monitoring."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from trantor.runtime.candles import RawCandle, analyse_candles
from trantor.runtime.market import FeedStatus, MarketDataMonitor
from trantor.runtime.mt5_gateway import MT5Gateway, mask_login
from trantor.runtime.timebase import BrokerTimeBasis, TimeProvenance, measure_offset

from runtime_helpers import FakeMT5, FakeSource, m15_series

UTC = timezone.utc
T0 = datetime(2026, 11, 4, 10, 0, 7, tzinfo=UTC)


# -- broker clock ---------------------------------------------------------------

def test_offset_measured_and_rounded_to_half_hours() -> None:
    m = measure_offset(T0 + timedelta(hours=2, seconds=-4), T0)
    assert m.offset_seconds == 7200
    assert m.residual_seconds == pytest.approx(-4)
    assert m.usable


def test_stale_tick_is_not_a_clock_sample() -> None:
    m = measure_offset(T0 + timedelta(hours=2) - timedelta(minutes=10), T0)
    assert not m.usable


def test_offset_verified_only_after_three_agreeing_samples() -> None:
    basis = BrokerTimeBasis()
    for index in range(2):
        basis.observe(T0 + timedelta(hours=2, seconds=index), T0 + timedelta(seconds=index))
        assert not basis.verified
        assert basis.offset is None
    basis.observe(T0 + timedelta(hours=2, seconds=2), T0 + timedelta(seconds=2))
    assert basis.verified
    assert basis.offset == timedelta(hours=2)
    assert basis.provenance is TimeProvenance.NORMALIZED_UTC_VERIFIED
    assert basis.to_utc(T0 + timedelta(hours=2)) == T0
    assert basis.to_server(T0) == T0 + timedelta(hours=2)


def test_offset_change_after_dst_is_recorded() -> None:
    basis = BrokerTimeBasis()
    for index in range(3):
        basis.observe(T0 + timedelta(hours=3, seconds=index), T0 + timedelta(seconds=index))
    assert basis.offset == timedelta(hours=3)
    later = T0 + timedelta(days=1)
    basis.observe(later + timedelta(hours=2), later)
    assert not basis.verified  # disagreement while the new offset is unconfirmed
    for index in range(1, 3):
        basis.observe(later + timedelta(hours=2, seconds=index), later + timedelta(seconds=index))
    assert basis.offset == timedelta(hours=2)
    assert basis.verified
    assert basis.offset_changes[-1]["new_offset_seconds"] == 7200


def test_unknown_offset_cannot_normalize() -> None:
    basis = BrokerTimeBasis()
    with pytest.raises(ValueError):
        basis.to_utc(T0)
    basis.assume(timedelta(0))
    assert not basis.verified
    with pytest.raises(ValueError):
        basis.assume(timedelta(minutes=7))


def test_naive_times_rejected() -> None:
    with pytest.raises(ValueError):
        measure_offset(datetime(2026, 1, 1), T0)


# -- candles --------------------------------------------------------------------

SERVER_START = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def test_forming_bar_is_never_completed() -> None:
    rows = m15_series(SERVER_START, 5)
    now = SERVER_START + timedelta(minutes=64)  # the 5th bar (09:60) is forming
    completed, forming, quality = analyse_candles(rows, symbol="XAUUSD-P", timeframe="15m", server_now=now)
    assert len(completed) == 4
    assert forming is not None and forming.observed_at == SERVER_START + timedelta(minutes=60)
    assert quality.forming and quality.clean


def test_bar_completes_exactly_at_its_close() -> None:
    rows = m15_series(SERVER_START, 2)
    now = SERVER_START + timedelta(minutes=30)
    completed, forming, _ = analyse_candles(rows, symbol="XAUUSD-P", timeframe="15m", server_now=now)
    assert len(completed) == 2 and forming is None


def test_shift_normalizes_to_utc() -> None:
    rows = m15_series(SERVER_START, 3)
    completed, _, _ = analyse_candles(
        rows,
        symbol="XAUUSD-P",
        timeframe="15m",
        server_now=SERVER_START + timedelta(hours=1),
        shift=timedelta(hours=2),
    )
    assert completed[0].observed_at == SERVER_START - timedelta(hours=2)


def test_duplicates_out_of_order_off_grid_and_invalid_rows_are_dropped_and_counted() -> None:
    rows = m15_series(SERVER_START, 4)
    bad = [
        rows[0],
        rows[1],
        rows[1],  # duplicate
        rows[3],
        rows[2],  # out of order
        RawCandle(time=int((SERVER_START + timedelta(minutes=67)).timestamp()), open=1, high=2, low=0.5, close=1.5),
        RawCandle(time=int((SERVER_START + timedelta(minutes=90)).timestamp()), open=math.nan, high=1, low=1, close=1),
        RawCandle(time=int((SERVER_START + timedelta(minutes=105)).timestamp()), open=math.inf, high=1, low=1, close=1),
        RawCandle(time=int((SERVER_START + timedelta(minutes=120)).timestamp()), open=-1, high=1, low=-2, close=1),
        RawCandle(time="yesterday", open=1, high=1, low=1, close=1),
        RawCandle(time=int((SERVER_START + timedelta(minutes=135)).timestamp()), open=10, high=9, low=8, close=9),
    ]
    completed, _, quality = analyse_candles(
        bad, symbol="XAUUSD-P", timeframe="15m", server_now=SERVER_START + timedelta(hours=5)
    )
    assert [bar.observed_at for bar in completed] == [SERVER_START, SERVER_START + timedelta(minutes=15), SERVER_START + timedelta(minutes=45)]
    assert quality.duplicates == 1
    assert quality.out_of_order == 1
    assert quality.off_grid == 1
    assert len(quality.invalid_rows) == 5
    assert not quality.clean
    assert quality.gaps and quality.gaps[0].missing_bars == 1


def test_missing_bars_reported_never_filled() -> None:
    rows = m15_series(SERVER_START, 6)
    del rows[2:4]
    completed, _, quality = analyse_candles(rows, symbol="XAUUSD-P", timeframe="15m", server_now=SERVER_START + timedelta(hours=3))
    assert len(completed) == 4
    assert quality.gaps[0].missing_bars == 2
    assert not quality.gaps[0].weekend_like


def test_weekend_gap_labelled_but_not_assumed_scheduled() -> None:
    friday = datetime(2026, 11, 6, 21, 45, tzinfo=UTC)
    rows = [
        RawCandle(time=int(friday.timestamp()), open=1, high=2, low=0.5, close=1.5),
        RawCandle(time=int((friday + timedelta(days=2, hours=2, minutes=30)).timestamp()), open=1, high=2, low=0.5, close=1.5),
    ]
    _, _, quality = analyse_candles(rows, symbol="XAUUSD-P", timeframe="15m", server_now=friday + timedelta(days=3))
    assert quality.gaps[0].weekend_like


def test_bar_from_the_future_is_rejected() -> None:
    rows = m15_series(SERVER_START, 3)
    completed, forming, quality = analyse_candles(
        rows, symbol="XAUUSD-P", timeframe="15m", server_now=SERVER_START + timedelta(minutes=5)
    )
    # 09:15 and 09:30 open in the broker's future: dropped and flagged.
    assert forming is not None and forming.observed_at == SERVER_START
    assert any("2 bar(s) open ahead of the broker clock" in item for item in quality.invalid_rows)
    assert len(completed) == 0 and not quality.clean


def test_dst_transition_week_d1_bars_validate() -> None:
    # Broker D1 bars across the 2026-11-01 US DST change stay on the server grid.
    rows = [
        RawCandle(time=int(datetime(2026, 10, day, tzinfo=UTC).timestamp()), open=1, high=2, low=0.5, close=1.5)
        for day in (29, 30)
    ] + [
        RawCandle(time=int(datetime(2026, 11, day, tzinfo=UTC).timestamp()), open=1, high=2, low=0.5, close=1.5)
        for day in (2, 3)
    ]
    completed, forming, quality = analyse_candles(rows, symbol="XAUUSD-P", timeframe="1d", server_now=datetime(2026, 11, 3, 12, tzinfo=UTC))
    assert len(completed) == 3 and forming is not None
    assert quality.clean and quality.gaps[0].weekend_like


# -- monitor --------------------------------------------------------------------

def _monitor(source: FakeSource) -> MarketDataMonitor:
    return MarketDataMonitor(source=source, timebase=BrokerTimeBasis(), host_clock=lambda: source.now)


def _warm(monitor: MarketDataMonitor, source: FakeSource) -> None:
    for _ in range(3):
        monitor.poll_tick()
        source.now += timedelta(seconds=1)


def test_monitor_becomes_live_after_clock_verification() -> None:
    source = FakeSource(now=T0)
    monitor = _monitor(source)
    monitor.poll_tick()
    assert monitor.status is FeedStatus.CLOCK_UNVERIFIED
    _warm(monitor, source)
    assert monitor.status is FeedStatus.LIVE
    snap = monitor.snapshot()
    assert snap["quote"]["bid"] == 2000.0 and snap["quote"]["label"] == "LIVE DATA"
    assert snap["time_basis"]["offset_hours"] == 2


def test_disconnected_feed_has_no_quote() -> None:
    source = FakeSource(now=T0)
    monitor = _monitor(source)
    _warm(monitor, source)
    source.connected = False
    monitor.poll_tick()
    assert monitor.status is FeedStatus.DISCONNECTED
    assert monitor.snapshot()["quote"] is None
    assert "market data feed is disconnected" in monitor.entry_blockers()


def test_frozen_ticks_become_stale_and_block_entries() -> None:
    source = FakeSource(now=T0)
    monitor = _monitor(source)
    _warm(monitor, source)
    source.frozen_tick = source.now + source.offset
    monitor.poll_tick()
    source.now += timedelta(minutes=5)
    monitor.poll_tick()
    assert monitor.status is FeedStatus.STALE
    assert monitor.snapshot()["quote"] is None
    assert any("stale" in reason for reason in monitor.entry_blockers())


def test_delayed_ticks_within_tolerance_stay_live() -> None:
    source = FakeSource(now=T0, tick_lag=timedelta(seconds=40))
    monitor = _monitor(source)
    _warm(monitor, source)
    assert monitor.status is FeedStatus.LIVE


def test_crossed_or_missing_quote_rejected() -> None:
    source = FakeSource(now=T0, bid=2001.0, ask=2000.0)
    monitor = _monitor(source)
    assert monitor.poll_tick() is None
    assert monitor.status is FeedStatus.NO_DATA
    source.bid = None
    source.ask = 2000.0
    assert monitor.poll_tick() is None


def test_rates_failure_drops_frames_and_blocks() -> None:
    source = FakeSource(now=T0)
    source.bars["15m"] = m15_series(T0 + source.offset - timedelta(hours=3), 12)
    monitor = _monitor(source)
    _warm(monitor, source)
    monitor.refresh(("15m",))
    assert "15m" in monitor.frames
    source.raise_rates = True
    monitor.refresh(("15m",))
    assert "15m" not in monitor.frames
    assert "15m candles unavailable" in monitor.entry_blockers()


def test_structural_completion_without_clock_keeps_newest_bar_forming() -> None:
    source = FakeSource(now=T0)
    source.bars["15m"] = m15_series(SERVER_START - timedelta(hours=10), 8)
    monitor = _monitor(source)
    monitor.refresh(("15m",))
    frame = monitor.frames["15m"]
    assert frame.completion_basis == "structural_unverified_clock"
    assert len(frame.completed) == 7 and frame.forming is not None


# -- gateway --------------------------------------------------------------------

def _gateway(fake: FakeMT5, clock: list[float]) -> MT5Gateway:
    events: list[tuple[str, str]] = []
    gateway = MT5Gateway(
        terminal_path="C:/fake/terminal64.exe",
        mt5_module=fake,
        clock=lambda: clock[0],
        on_event=lambda level, event, detail: events.append((level, event)),
    )
    gateway.events = events  # type: ignore[attr-defined]
    return gateway


def test_gateway_has_no_order_operations() -> None:
    gateway = _gateway(FakeMT5(), [0.0])
    for name in ("order_send", "submit_order", "positions_get", "close_position"):
        assert not hasattr(gateway, name)


def test_gateway_backoff_and_reconnect() -> None:
    fake = FakeMT5()
    fake.initialize_ok = False
    clock = [0.0]
    gateway = _gateway(fake, clock)
    assert gateway.connect() is False
    assert gateway.connect() is False  # inside backoff window: no new attempt
    assert fake.calls.count("initialize") == 1
    clock[0] = 10.0
    fake.initialize_ok = True
    assert gateway.connect() is True
    assert gateway.reconnect_count == 1
    assert ("info", "mt5_reconnected") in gateway.events  # type: ignore[attr-defined]


def test_gateway_detects_broker_disconnect_and_recovers() -> None:
    fake = FakeMT5()
    clock = [0.0]
    gateway = _gateway(fake, clock)
    assert gateway.health()["connected"]
    fake.connected = False
    assert gateway.health()["connected"] is False
    assert ("error", "mt5_disconnected") in gateway.events  # type: ignore[attr-defined]
    fake.connected = True
    clock[0] = 5.0
    assert gateway.health()["connected"]


def test_gateway_exception_marks_disconnected() -> None:
    from trantor.market_data.providers import ProviderUnavailableError

    fake = FakeMT5()
    gateway = _gateway(fake, [0.0])
    gateway.connect()
    fake.raise_on.add("symbol_info_tick")
    with pytest.raises(ProviderUnavailableError):
        gateway.tick()
    assert not gateway.connected


def test_gateway_masks_account_login_and_reads_tick_and_rates() -> None:
    fake = FakeMT5()
    fake.tick_value = {"time": 1_793_000_000, "time_msc": 1_793_000_000_123, "bid": 2000.0, "ask": 2000.25, "last": 0.0}
    fake.rates_rows[fake.TIMEFRAME_M15] = [
        {"time": 1_793_000_000 - 900, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "tick_volume": 10, "spread": 20}
    ]
    gateway = _gateway(fake, [0.0])
    acct = gateway.account()
    assert acct["login_masked"] == "*****678" and "login" not in acct
    assert acct["is_demo"] is True
    tick = gateway.tick()
    assert tick.raw_time.microsecond == 123000 and tick.spread == pytest.approx(0.25)
    rows = gateway.rates("15m", 10)
    assert rows[0].tick_volume == 10
    assert mask_login(None) is None and mask_login(12) == "***"


def test_gateway_symbol_unavailable_fails_connect() -> None:
    fake = FakeMT5()
    gateway = MT5Gateway(terminal_path="x", symbol="GOLD", mt5_module=fake, clock=lambda: 0.0)
    assert gateway.connect() is False
    assert "cannot be selected" in gateway.last_error


def test_forming_bar_slightly_ahead_of_estimated_clock_is_accepted() -> None:
    # Observed live: the broker clock leads host+offset by ~0.5 s at a bar boundary.
    rows = m15_series(SERVER_START, 3)
    now = SERVER_START + timedelta(minutes=30) - timedelta(milliseconds=500)
    completed, forming, quality = analyse_candles(rows, symbol="XAUUSD-P", timeframe="15m", server_now=now)
    assert forming is not None and forming.observed_at == SERVER_START + timedelta(minutes=30)
    assert quality.clean and len(completed) == 2  # 09:15 is complete: the broker opened 09:30


def test_server_clock_estimate_uses_newest_tick() -> None:
    source = FakeSource(now=T0)
    monitor = _monitor(source)
    _warm(monitor, source)
    source.tick_lag = -timedelta(seconds=2)  # broker clock leads the host
    monitor.poll_tick()
    assert monitor.server_now() == source.now + source.offset + timedelta(seconds=2)
