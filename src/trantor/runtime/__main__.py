"""Trantor runtime command line.

Examples (PowerShell, repository root)::

    $env:PYTHONPATH='src'
    python -m trantor.runtime run                 # shadow or paper per settings
    python -m trantor.runtime status
    python -m trantor.runtime verify-broker
    python -m trantor.runtime stop-entries --reason "manual halt"
    python -m trantor.runtime resume-entries --confirm

``run`` refuses any execution mode other than ``research`` (shadow) and
``paper``. No command in this module can submit a broker order.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import ConfigurationError, ExecutionMode, load_settings
from ..execution.gate import KillSwitch
from ..monitoring.logs import configure_logging
from ..monitoring.notify import Notifier, TelegramTransport
from .store import RuntimeStore, StoreError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE = REPO_ROOT / "state"
DEFAULT_CALENDAR = REPO_ROOT / "config" / "news_calendar.json"
DEFAULT_EXPECTATIONS = REPO_ROOT / "config" / "broker_expectations.json"
_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TRANTOR_SETTINGS_PATH", "TRANTOR_LOG_LEVEL", "TRANTOR_STATE_DIR")


def load_dotenv(path: Path) -> None:
    """Read KEY=VALUE pairs for known keys without overriding the environment."""

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in _ENV_KEYS and key not in os.environ and value.strip():
            os.environ[key] = value.strip().strip('"').strip("'")


def _state_dir(args: argparse.Namespace) -> Path:
    if args.state_dir:
        return Path(args.state_dir)
    return Path(os.environ.get("TRANTOR_STATE_DIR", str(DEFAULT_STATE)))


def _build_notifier(store: RuntimeStore) -> Notifier:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    transport = TelegramTransport(token, chat) if token and chat else None
    return Notifier(store, transport, secrets=[token, chat])


def cmd_run(args: argparse.Namespace) -> int:
    from .mt5_gateway import MT5Gateway
    from .service import LockError, RuntimeConfig, TrantorRuntime

    settings = load_settings(args.settings)
    if settings.execution_mode not in (ExecutionMode.RESEARCH, ExecutionMode.PAPER):
        print(
            f"Refusing to run: execution_mode={settings.execution_mode.value!r}. "
            "Set 'research' (shadow) or 'paper' in the settings file.",
            file=sys.stderr,
        )
        return 2
    if settings.market_data_provider != "mt5" or not settings.mt5_terminal_path:
        print("Refusing to run: market_data_provider must be 'mt5' with mt5_terminal_path set.", file=sys.stderr)
        return 2
    state_dir = _state_dir(args)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    # One log directory per state directory: two runtimes never share (or
    # rotate) the same file.
    configure_logging(REPO_ROOT / "logs" / state_dir.name, settings.log_level, secrets=[token])
    config = RuntimeConfig(
        settings=settings,
        state_dir=state_dir,
        calendar_path=Path(args.calendar) if args.calendar else (DEFAULT_CALENDAR if DEFAULT_CALENDAR.exists() else None),
        expectations_path=Path(args.expectations) if args.expectations else DEFAULT_EXPECTATIONS,
    )
    gateway = MT5Gateway(terminal_path=settings.mt5_terminal_path)
    runtime = TrantorRuntime(config=config, source=gateway, notifier_factory=_build_notifier)
    try:
        runtime.start()
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    stop = threading.Event()

    def _handle(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle)
    signal.signal(signal.SIGTERM, _handle)
    print(f"Trantor runtime started in {runtime.engine_config.profile} mode. State: {state_dir}")
    print("No broker order route exists in this mode. Press Ctrl+C to stop.")
    deadline = None
    if args.duration_minutes:
        deadline = datetime.now(timezone.utc).timestamp() + args.duration_minutes * 60
    try:
        while not stop.is_set():
            runtime.step()
            if deadline is not None and datetime.now(timezone.utc).timestamp() >= deadline:
                break
            stop.wait(config.poll_interval)
    finally:
        runtime.shutdown("duration elapsed" if deadline else "signal or operator stop")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = _state_dir(args) / "trantor_runtime.sqlite3"
    try:
        store = RuntimeStore(path, read_only=True)
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    status = store.get_state("runtime_status")
    print(json.dumps(status, indent=2, default=str) if status else "no runtime status recorded")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .broker_verification import commission_window, load_expectations, proposed_expectations, verify_broker
    from .market import MarketDataMonitor
    from .mt5_gateway import MT5Gateway
    from .timebase import BrokerTimeBasis
    import time

    settings = load_settings(args.settings)
    if not settings.mt5_terminal_path:
        print("mt5_terminal_path is not configured", file=sys.stderr)
        return 2
    gateway = MT5Gateway(terminal_path=settings.mt5_terminal_path)
    timebase = BrokerTimeBasis()
    monitor = MarketDataMonitor(source=gateway, timebase=timebase)
    try:
        for _ in range(args.samples):
            monitor.poll_tick()
            time.sleep(1.5)
        monitor.refresh(("15m",))
        tick = monitor.last_tick
        frame = monitor.frames.get("15m")
        expectations = load_expectations(args.expectations or DEFAULT_EXPECTATIONS)
        now = datetime.now(timezone.utc)
        since, until = commission_window(now)
        report = verify_broker(
            symbol=gateway.symbol,
            symbol_info=gateway.symbol_info(),
            account=gateway.account(),
            tick=None if tick is None else {"bid": tick.bid, "ask": tick.ask},
            calc_profit=gateway.calc_profit,
            calc_margin=gateway.calc_margin,
            expectations=expectations,
            forming_m15_close=None if frame is None or frame.forming is None else frame.forming.close,
            commissions=gateway.deal_commissions(since, until),
            time_basis=timebase.snapshot(),
            now=now,
        )
    finally:
        gateway.close()
    state_dir = _state_dir(args)
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = state_dir / f"broker_verification_{stamp}.json"
    out.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
    (state_dir / "broker_expectations.proposed.json").write_text(
        json.dumps(proposed_expectations(report), indent=2, default=str), encoding="utf-8"
    )
    store = RuntimeStore(state_dir / "trantor_runtime.sqlite3")
    store.record_verification(report.report_id, report.overall, report.as_dict())
    store.close()
    print(json.dumps({"overall": report.overall, "report": str(out), "time_basis": timebase.snapshot()}, indent=2, default=str))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    switch = KillSwitch(_state_dir(args) / "EMERGENCY_STOP")
    payload = switch.engage(args.reason, actor=os.environ.get("USERNAME", "operator"))
    print(f"Emergency stop ENGAGED: {payload}. New entries are blocked; position monitoring continues.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("Refusing: pass --confirm to release the emergency stop.", file=sys.stderr)
        return 2
    switch = KillSwitch(_state_dir(args) / "EMERGENCY_STOP")
    released = switch.release()
    print("Emergency stop released." if released else "Emergency stop was not engaged.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trantor.runtime", description="Trantor runtime (shadow/paper only)")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--settings", default=None, help="settings JSON (default TRANTOR_SETTINGS_PATH or config/settings.json)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the shadow/paper runtime")
    run.add_argument("--calendar", default=None)
    run.add_argument("--expectations", default=None)
    run.add_argument("--duration-minutes", type=float, default=None)
    run.set_defaults(func=cmd_run)
    status = sub.add_parser("status", help="print the last runtime status")
    status.set_defaults(func=cmd_status)
    verify = sub.add_parser("verify-broker", help="read-only broker metadata verification")
    verify.add_argument("--expectations", default=None)
    verify.add_argument("--samples", type=int, default=5)
    verify.set_defaults(func=cmd_verify)
    stop = sub.add_parser("stop-entries", help="engage the emergency stop")
    stop.add_argument("--reason", required=True)
    stop.set_defaults(func=cmd_stop)
    resume = sub.add_parser("resume-entries", help="release the emergency stop")
    resume.add_argument("--confirm", action="store_true")
    resume.set_defaults(func=cmd_resume)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
