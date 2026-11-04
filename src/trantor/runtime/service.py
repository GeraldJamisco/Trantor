"""Trantor runtime service: scheduling, recovery, monitoring and shutdown.

One process, one thread for trading work. Every scheduled task runs inside
:meth:`TrantorRuntime.step`, so evaluations can never overlap; a file lock
prevents a second process from running against the same state directory.

Startup sequence (``state_interfaces_persistence.md`` §5.4):

1. validate settings and mode; acquire the process lock;
2. open/migrate SQLite;
3. load positions and risk state (fail closed if inconsistent);
4. reconcile orders/positions/risk; never resubmit an unknown order;
5. run broker verification once connected;
6. notify the operator and start the loop.

Emergency stop blocks new entries only. Position monitoring, exit management
and risk enforcement keep running.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import ExecutionMode, Settings, settings_fingerprint, settings_snapshot
from ..execution.gate import EffectiveLimits, ExecutionGate, KillSwitch
from ..execution.paper import PaperBroker
from ..models import MarketObservation
from ..strategy.xauusd_p import XAUUSDPMultiTimeframeStrategy
from .broker_verification import (
    BrokerVerificationReport,
    commission_window,
    load_expectations,
    proposed_expectations,
    verify_broker,
)
from .calendar import CalendarSnapshot, FileCalendarProvider, UnavailableCalendarProvider, evaluate_news
from .candles import TIMEFRAME_DURATION
from .engine import CycleInputs, DecisionEngine, EngineConfig, reconcile
from .market import FeedStatus, MarketDataMonitor
from .positions import PositionManager
from .risk import RiskStateError, RiskTracker
from .sessions import session_snapshot
from .store import RuntimeStore, StoreError
from .timebase import BrokerTimeBasis

__all__ = ["ProcessLock", "RuntimeConfig", "TrantorRuntime", "LockError"]

UTC = timezone.utc
LOGGER = logging.getLogger("trantor.runtime")
M15 = timedelta(minutes=15)
STRATEGY_M15_WINDOW = 64  # identical to the Phase E HistoricalView lookback


class LockError(RuntimeError):
    """Another Trantor runtime holds the state directory."""


class ProcessLock:
    """Exclusive OS-level lock on ``<state>/runtime.lock``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise LockError(f"another Trantor runtime is using {self.path.parent}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "since": datetime.now(UTC).isoformat()}))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None


def _process_memory_mb() -> float | None:
    if os.name != "nt":
        try:
            import resource

            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return None
    try:
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        get_info.restype = ctypes.c_int
        if get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / 1_048_576, 1)
    except Exception:
        return None
    return None


@dataclass
class RuntimeConfig:
    settings: Settings
    state_dir: Path
    calendar_path: Path | None = None
    expectations_path: Path | None = None
    poll_interval: float = 1.0
    bar_refresh_interval: float = 5.0
    status_interval: float = 2.0
    verification_interval: float | None = 6 * 3600.0
    calendar_refresh_interval: float = 60.0
    entry_window: timedelta = timedelta(seconds=120)
    bar_counts: dict[str, int] = field(default_factory=lambda: {"1d": 2000, "4h": 600, "15m": 200})
    equity_model: str = "paper"
    starting_equity: float = 10_000.0
    slow_refresh: timedelta = timedelta(minutes=5)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "trantor_runtime.sqlite3"

    @property
    def kill_switch_path(self) -> Path:
        return self.state_dir / "EMERGENCY_STOP"


@dataclass
class TrantorRuntime:
    config: RuntimeConfig
    source: Any
    notifier: Any = None
    notifier_factory: Callable[[RuntimeStore], Any] | None = None
    host_clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    monotonic: Callable[[], float] = _time.monotonic
    strategy: XAUUSDPMultiTimeframeStrategy = field(default_factory=XAUUSDPMultiTimeframeStrategy)

    store: RuntimeStore | None = None
    lock: ProcessLock | None = None
    started_at: datetime | None = None
    last_decision_time: datetime | None = None
    last_result: dict[str, Any] | None = None
    stats: dict[str, Any] = field(default_factory=lambda: {
        "steps": 0,
        "evaluations": 0,
        "duplicates_skipped": 0,
        "missed_evaluations": 0,
        "step_errors": 0,
        "max_step_seconds": 0.0,
    })
    _calendar_snapshots: list[CalendarSnapshot] = field(default_factory=list)
    _next: dict[str, float] = field(default_factory=dict)
    _verification: BrokerVerificationReport | None = None
    _entry_halt_reason: str | None = None
    _summary_day: str | None = None

    # -- setup --------------------------------------------------------------

    @property
    def mode(self) -> ExecutionMode:
        return self.config.settings.execution_mode

    def start(self) -> None:
        if self.mode not in (ExecutionMode.RESEARCH, ExecutionMode.PAPER):
            raise RuntimeError(
                f"execution_mode={self.mode.value!r}: the runtime only runs in 'research' "
                "(shadow) or 'paper' mode; demo and live are not authorized"
            )
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = ProcessLock(self.config.state_dir / "runtime.lock")
        self.lock.acquire()
        try:
            self.store = RuntimeStore(self.config.db_path)
            if self.notifier is None and self.notifier_factory is not None:
                self.notifier = self.notifier_factory(self.store)
            if hasattr(self.source, "set_event_handler"):
                self.source.set_event_handler(self._event)
            snapshot = settings_snapshot(self.config.settings)
            self.store.record_config(settings_fingerprint(self.config.settings), snapshot)
            self.timebase = BrokerTimeBasis()
            self.monitor = MarketDataMonitor(
                source=self.source,
                timebase=self.timebase,
                host_clock=self.host_clock,
                on_event=self._event,
            )
            self.monitor.bar_counts = dict(self.config.bar_counts)
            self.monitor.slow_refresh = self.config.slow_refresh
            self.engine_config = EngineConfig(
                execution_mode=self.mode,
                config_fingerprint=settings_fingerprint(self.config.settings),
                entry_window=self.config.entry_window,
            )
            profile = self.engine_config.profile
            self.tracker = RiskTracker(
                self.store,
                profile,
                self.strategy.parameters,
                starting_equity=self.config.starting_equity,
                equity_model=self.config.equity_model,
            )
            self.positions = PositionManager(self.store, self.tracker, profile)
            self.positions.load()
            self.kill_switch = KillSwitch(self.config.kill_switch_path)
            limits = EffectiveLimits.combine(self.config.settings, self.strategy.parameters)
            self.gate = ExecutionGate(self.config.settings, self.kill_switch, limits)
            self.broker = PaperBroker() if profile == "paper" else None
            self.engine = DecisionEngine(
                store=self.store,
                strategy=self.strategy,
                config=self.engine_config,
                tracker=self.tracker,
                positions=self.positions,
                gate=self.gate,
                broker=self.broker,
                notifier=self.notifier,
            )
            self.calendar = (
                FileCalendarProvider(self.config.calendar_path)
                if self.config.calendar_path is not None
                else UnavailableCalendarProvider()
            )
            self._recover()
            last = self.store.recent_decisions(1)
            if last:
                self.last_decision_time = datetime.fromisoformat(last[0]["decision_time"])
            self.started_at = self.host_clock()
            if self.notifier is not None:
                self.notifier.start()
            self._event("info", "runtime_started", {"mode": self.mode.value, "profile": profile})
            self._notify("lifecycle", f"start:{self.started_at.isoformat()}", f"Trantor started in {profile} mode ({self.mode.value}). No broker orders are sent.")
        except Exception:
            self.lock.release()
            raise

    def _recover(self) -> None:
        assert self.store is not None
        self._entry_halt_reason = None
        try:
            self.tracker.load(self.host_clock(), open_positions=self.positions.open_count())
        except RiskStateError as exc:
            self._halt_entries(f"risk state: {exc}")
            return
        consistent, detail = reconcile(self.store, self.engine_config.profile, self.tracker, self.positions)
        self.engine.reconciled = consistent
        if not consistent:
            self._halt_entries("reconciliation failed: " + "; ".join(detail["issues"]))
        else:
            self._event("info", "reconciliation_ok", detail)

    def _halt_entries(self, reason: str) -> None:
        self._entry_halt_reason = reason
        self.engine.reconciled = False
        self._event("critical", "entries_halted", {"reason": reason})
        self._notify("critical", f"halt:{reason}", f"CRITICAL: new entries halted - {reason}")

    # -- events -------------------------------------------------------------

    def _event(self, level: str, event: str, detail: dict[str, Any]) -> None:
        log = getattr(LOGGER, "critical" if level == "critical" else level, LOGGER.info)
        log("%s %s", event, json.dumps(detail, default=str)[:2000])
        if self.store is not None:
            try:
                self.store.record_health(level, "runtime", event, detail)
            except StoreError:
                LOGGER.exception("could not record health event")
        if event in ("mt5_disconnected", "mt5_reconnected") or (event == "feed_status_changed" and detail.get("to") in ("disconnected", "live")):
            stamp = self.host_clock().strftime("%Y%m%dT%H%M")
            self._notify("market", f"{event}:{detail.get('to', '')}:{stamp}", f"Market data: {event} {detail.get('to', '')} {detail.get('error') or detail.get('reason') or ''}".strip())

    def _notify(self, kind: str, key: str, text: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.notify(kind, key, text)
        except Exception:
            LOGGER.exception("notification failed")

    # -- scheduling ----------------------------------------------------------

    def _due(self, name: str, interval: float) -> bool:
        now = self.monotonic()
        if now >= self._next.get(name, 0.0):
            self._next[name] = now + interval
            return True
        return False

    def step(self) -> None:
        started = self.monotonic()
        self.stats["steps"] += 1
        try:
            self.monitor.poll_tick()
            if self._due("calendar", self.config.calendar_refresh_interval):
                self._refresh_calendar()
            if self._due("bars", self.config.bar_refresh_interval):
                self.monitor.refresh()
                self._process_bars_and_decide()
            if (
                self.config.verification_interval is not None
                and self.monitor.status is FeedStatus.LIVE
                and self._due("verify", self.config.verification_interval)
            ):
                self.run_verification()
            self._daily_summary()
            if self._due("status", self.config.status_interval):
                self.write_status()
        except StoreError as exc:
            self.stats["step_errors"] += 1
            self._halt_entries(f"storage failure: {exc}")
        except RiskStateError as exc:
            self.stats["step_errors"] += 1
            self._halt_entries(f"risk state: {exc}")
        except Exception as exc:
            self.stats["step_errors"] += 1
            self._event("error", "step_failed", {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            elapsed = self.monotonic() - started
            self.stats["max_step_seconds"] = max(self.stats["max_step_seconds"], round(elapsed, 3))

    def _refresh_calendar(self) -> None:
        snapshot = self.calendar.refresh(self.host_clock())
        self._calendar_snapshots.append(snapshot)
        del self._calendar_snapshots[:-30]

    def _calendar_for(self, decision_time: datetime) -> CalendarSnapshot | None:
        # Use the newest snapshot retrieved at or before the decision; for a
        # static owner-confirmed schedule a later re-read is also acceptable.
        before = [snap for snap in self._calendar_snapshots if snap.retrieved_at <= decision_time]
        if before:
            return before[-1]
        return self._calendar_snapshots[-1] if self._calendar_snapshots else None

    def _process_bars_and_decide(self) -> None:
        frame = self.monitor.frames.get("15m")
        if frame is None or not frame.completed:
            return
        if frame.completion_basis != "broker_clock":
            return
        # 1. Exit management on completed bars (runs even under emergency stop).
        events = self.positions.on_completed_bars(frame.completed)
        for event in events:
            position = event.position
            pnl = event.exit.contribution * position.risk_amount
            self._event("info", "position_exit", {"position_id": position.position_id, "reason": event.exit.reason, "price": event.exit.price, "r": event.exit.r_multiple})
            label = "PAPER" if position.mode == "paper" else "SHADOW"
            text = (
                f"{label} {position.side.value.upper()} {event.exit.reason} at {event.exit.price} "
                f"({event.exit.fraction:.0%} of position, {event.exit.r_multiple:+.2f}R, {pnl:+.2f} paper units)"
            )
            if event.closed:
                text += f"; trade closed {position.realized_r:+.2f}R"
            self._notify("paper_exit", f"{position.position_id}:{event.exit.reason}:{event.exit.bar_time.isoformat()}", text)
        if self.tracker.book is not None and self.tracker.book.daily_locked_out:
            self._notify("risk", f"lockout:{self.tracker.book.day}", f"Daily loss limit reached ({self.tracker.book.lockout_reason}); no new entries until the next UTC day.")

        # 2. Decision timing: the trigger is the newest completed bar and the
        #    next bar must already have opened (its open is the entry reference).
        trigger = frame.completed[-1]
        decision_time = trigger.observed_at + M15
        if self.last_decision_time is not None and decision_time <= self.last_decision_time:
            return
        forming = frame.forming
        if forming is None:
            return  # next bar has not opened yet (or market closed); wait.
        # Only the next bar's open is known at entry time; the stub carries no
        # future high/low/close. A non-adjacent bar is passed through so the
        # strategy itself rejects it, exactly as the backtester does.
        next_bar = MarketObservation(
            symbol=forming.symbol,
            timeframe="15m",
            observed_at=forming.observed_at,
            open=forming.open,
            high=forming.open,
            low=forming.open,
            close=forming.open,
            volume=None,
        )
        if self.last_decision_time is not None and decision_time - self.last_decision_time > M15:
            missed = int((decision_time - self.last_decision_time) / M15) - 1
            self.stats["missed_evaluations"] += missed
            self._event("warning", "evaluations_not_run", {"count": missed, "from": self.last_decision_time.isoformat(), "to": decision_time.isoformat(), "note": "includes closed-market periods; not back-filled"})
        self._evaluate(decision_time, frame.completed, next_bar)

    def _evaluate(self, decision_time: datetime, m15: tuple[MarketObservation, ...], next_bar: MarketObservation | None) -> None:
        d1_frame = self.monitor.frames.get("1d")
        h4_frame = self.monitor.frames.get("4h")
        blockers = list(self.monitor.entry_blockers())
        if self._entry_halt_reason:
            blockers.append(f"entries halted: {self._entry_halt_reason}")
        news = evaluate_news(self._calendar_for(decision_time), decision_time=decision_time)
        tick = self.monitor.last_tick
        inputs = CycleInputs(
            decision_time=decision_time,
            decision_time_raw=self.timebase.to_server(decision_time) if self.timebase.offset is not None else None,
            d1=() if d1_frame is None else d1_frame.completed,
            h4=() if h4_frame is None else h4_frame.completed,
            m15=m15[-STRATEGY_M15_WINDOW:],
            next_bar=next_bar,
            evaluated_at=self.host_clock(),
            data_identity=f"live:mt5:{self.monitor.symbol}:server_offset={self.timebase.snapshot()['offset_seconds']}",
            news=news,
            blockers=tuple(blockers),
            time_basis=self.timebase.snapshot(),
            broker_verification=self._verification.overall if self._verification else "not_run",
            mark_bid=None if tick is None else tick.bid,
            mark_ask=None if tick is None else tick.ask,
        )
        result = self.engine.process(inputs)
        self.last_decision_time = decision_time
        if result.duplicate:
            self.stats["duplicates_skipped"] += 1
            return
        self.stats["evaluations"] += 1
        decision = result.decision
        self.last_result = {
            "decision_id": result.decision_id,
            "decision_time": decision_time.isoformat(),
            "status": None if decision is None else decision.status.value,
            "score": None if decision is None else decision.score.total,
            "rejections": [] if decision is None else [r.code.value for r in decision.rejections],
            "outcome": result.outcome,
            "blocked": result.blocked_reasons,
        }
        self._event("info", "decision", self.last_result)

    # -- verification ------------------------------------------------------

    def run_verification(self) -> BrokerVerificationReport | None:
        try:
            info = self.source.symbol_info()
            account = self.source.account()
            tick = self.monitor.last_tick
            expectations = None
            if self.config.expectations_path is not None:
                try:
                    expectations = load_expectations(self.config.expectations_path)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._event("error", "broker_expectations_invalid", {"error": str(exc)})
            frame = self.monitor.frames.get("15m")
            since, until = commission_window(self.host_clock())
            try:
                commissions = self.source.deal_commissions(since, until)
            except Exception:
                commissions = []
            report = verify_broker(
                symbol=self.monitor.symbol,
                symbol_info=info,
                account=account,
                tick=None if tick is None else {"bid": tick.bid, "ask": tick.ask},
                calc_profit=self.source.calc_profit,
                calc_margin=self.source.calc_margin,
                expectations=expectations,
                forming_m15_close=None if frame is None or frame.forming is None else frame.forming.close,
                commissions=commissions,
                time_basis=self.timebase.snapshot(),
                now=self.host_clock(),
            )
        except Exception as exc:
            self._event("error", "broker_verification_failed", {"error": f"{type(exc).__name__}: {exc}"})
            return None
        self._verification = report
        assert self.store is not None
        self.store.record_verification(report.report_id, report.overall, report.as_dict())
        proposal = self.config.state_dir / "broker_expectations.proposed.json"
        proposal.write_text(json.dumps(proposed_expectations(report), indent=2, default=str), encoding="utf-8")
        self._event("info", "broker_verification", {"overall": report.overall, "report_id": report.report_id})
        if not report.identity_ok:
            self._halt_entries("broker symbol identity check failed")
        return report

    # -- reporting -----------------------------------------------------------

    def _daily_summary(self) -> None:
        book = self.tracker.book
        if book is None:
            return
        if self._summary_day is None:
            self._summary_day = book.day
            return
        today = self.host_clock().astimezone(UTC).date().isoformat()
        if today == self._summary_day:
            return
        assert self.store is not None
        rows = [row for row in self.store.daily(self.engine_config.profile, 5) if row["day"] == self._summary_day]
        if rows:
            row = rows[0]
            self._notify(
                "daily_summary",
                self._summary_day,
                (
                    f"Daily summary {self._summary_day} ({self.engine_config.profile}): entries {row['entries']}, "
                    f"realized {row['realized_r']:+.2f}R / {row['realized_pnl']:+.2f} paper units, "
                    f"losses {row['losses']}, lockout {'yes' if row['locked_out'] else 'no'}"
                ),
            )
        self._summary_day = today

    def status(self) -> dict[str, Any]:
        assert self.store is not None
        tick = self.monitor.last_tick
        _, unrealized_pnl, complete = self.positions.unrealized_total(
            None if tick is None else tick.bid, None if tick is None else tick.ask
        )
        now = self.host_clock()
        open_positions = []
        for position in self.positions.open.values():
            r_value, pnl = position.unrealized(None if tick is None else tick.bid, None if tick is None else tick.ask)
            state = position.to_state()
            state["unrealized_r"] = r_value
            state["unrealized_pnl"] = pnl
            state["age_minutes"] = round((now - position.entry_time).total_seconds() / 60, 1)
            open_positions.append(state)
        return {
            "generated_at": now.isoformat(),
            "pid": os.getpid(),
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "uptime_seconds": None if self.started_at is None else round((now - self.started_at).total_seconds()),
            "execution_mode": self.mode.value,
            "profile": self.engine_config.profile,
            "labels": self.engine_config.labels,
            "strategy": {
                "variant_id": self.strategy.identity.variant_id,
                "name": self.strategy.identity.strategy_name,
                "version": self.strategy.identity.strategy_version,
                "parameters_fingerprint": self.engine.parameters_fingerprint,
            },
            "config_fingerprint": self.engine_config.config_fingerprint,
            "kill_switch": self.kill_switch.status(),
            "entries_halted": self._entry_halt_reason,
            "reconciled": self.engine.reconciled,
            "market": self.monitor.snapshot(),
            "session": session_snapshot(now, self.strategy.parameters, feed_live=self.monitor.status is FeedStatus.LIVE),
            "risk": None if self.tracker.book is None else self.tracker.status(now, unrealized_pnl if complete else 0.0),
            "open_positions": open_positions,
            "last_decision": self.last_result,
            "last_decision_time": None if self.last_decision_time is None else self.last_decision_time.isoformat(),
            "broker_verification": None if self._verification is None else {
                "overall": self._verification.overall,
                "report_id": self._verification.report_id,
                "generated_at": self._verification.generated_at.isoformat(),
            },
            "calendar": None if not self._calendar_snapshots else self._calendar_snapshots[-1].as_dict(),
            "notifications": None if self.notifier is None else {
                "enabled": getattr(self.notifier, "enabled", False),
                "sent": getattr(self.notifier, "sent", 0),
                "failures": getattr(self.notifier, "failures", 0),
                "dropped": getattr(self.notifier, "dropped", 0),
            },
            "stats": {**self.stats, "memory_mb": _process_memory_mb()},
        }

    def live_candles(self, limit: int = 160) -> dict[str, Any]:
        """Recent completed bars plus the forming bar per timeframe (normalized UTC)."""

        result: dict[str, Any] = {"generated_at": self.host_clock().isoformat(), "frames": {}}
        for name, frame in self.monitor.frames.items():
            bars = [
                {"t": bar.observed_at.isoformat(), "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close, "v": bar.volume, "complete": True}
                for bar in frame.completed[-limit:]
            ]
            if frame.forming is not None:
                bar = frame.forming
                bars.append({"t": bar.observed_at.isoformat(), "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close, "v": bar.volume, "complete": False})
            result["frames"][name] = {
                "completion_basis": frame.completion_basis,
                "time_basis_verified": self.timebase.verified,
                "bars": bars,
            }
        return result

    def write_status(self) -> None:
        assert self.store is not None
        self.store.set_state("runtime_status", self.status())
        self.store.set_state("live_candles", self.live_candles())

    # -- loop ------------------------------------------------------------------

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.step()
            stop.wait(self.config.poll_interval)

    def shutdown(self, reason: str = "operator request") -> None:
        try:
            self._event("info", "runtime_stopping", {"reason": reason})
            self._notify("lifecycle", f"stop:{self.host_clock().isoformat()}", f"Trantor stopping ({reason}). Open {self.engine_config.profile} positions: {self.positions.open_count()}. Positions are not closed by shutdown.")
            if self.store is not None:
                status = self.status()
                status["stopped_at"] = self.host_clock().isoformat()
                self.store.set_state("runtime_status", status)
        finally:
            if self.notifier is not None:
                self.notifier.stop()
            try:
                self.source.close()
            except Exception:
                pass
            if self.store is not None:
                self.store.close()
            if self.lock is not None:
                self.lock.release()


def python_info() -> str:
    return sys.version.split()[0]
