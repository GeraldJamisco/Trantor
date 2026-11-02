"""Read-only MetaTrader 5 diagnostic probe.

This file is deliberately outside ``src/trantor`` and is not imported by the
runtime package.  It exists only to inspect an existing MT5 terminal/account
and its symbol/data availability.

READ-ONLY CONTRACT
------------------
The probe only uses connection, account/terminal status, symbol metadata,
latest-tick, and historical-bar functions.  It does not contain or invoke
trading, order, position, deal-history, or Market Watch mutation functions.
It never asks for or stores account credentials.

It intentionally does not call ``login``: it attaches to the account already
active in the running terminal, so no credential is handled by this script.
It also does not change terminal security settings or enable automated trading.

Usage examples (Windows):

    python tools/mt5_readonly_probe.py
    python tools/mt5_readonly_probe.py --bars 10
    python tools/mt5_readonly_probe.py --symbol XAUUSD --symbol GC --symbol MGC
    python tools/mt5_readonly_probe.py --list-all-symbols --sample-other 20
    python tools/mt5_readonly_probe.py --launch

If the terminal is not running, the default behavior is to stop and ask the
operator to start it.  ``--launch`` is an explicit opt-in that lets
``initialize()`` start the terminal as documented by MetaQuotes.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TERMINAL_CANDIDATES = (
    Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
    Path(r"C:\Program Files\FPG MetaTrader 5\terminal64.exe"),
    Path(r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"),
    Path(r"C:\Program Files (x86)\FPG MetaTrader 5\terminal64.exe"),
)

GOLD_TEXT_TOKENS = ("XAU", "GOLD")
GOLD_SYMBOL_PREFIXES = ("GC", "MGC")

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_PACKAGE_MISSING = 2
EXIT_ENVIRONMENT = 3
EXIT_INITIALIZE = 4
EXIT_ACCOUNT = 5
EXIT_SYMBOLS = 6


def _field(obj: Any, name: str) -> Any:
    """Return a named-tuple/object field without assuming it exists."""

    return getattr(obj, name, None)


def _record_field(record: Any, name: str) -> Any:
    """Return a field from a NumPy structured scalar or equivalent record."""

    try:
        return record[name]
    except (KeyError, TypeError, IndexError, ValueError):
        return getattr(record, name, None)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _fmt_epoch(value: Any) -> str:
    if value is None or value == 0:
        return "-"
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        return str(value)


def _mask_login(login: Any) -> str:
    if login is None:
        return "-"
    text = str(login)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _last_error(mt5: Any) -> tuple[Any, Any]:
    try:
        result = mt5.last_error()
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        return ("<unavailable>", str(exc))
    if isinstance(result, tuple) and len(result) >= 2:
        return result[0], result[1]
    return result, ""


def _error_text(mt5: Any) -> str:
    code, description = _last_error(mt5)
    return f"code={_fmt(code)} description={_fmt(description)}"


def _enum_label(mt5: Any, value: Any, names: tuple[str, ...]) -> str:
    if value is None:
        return "-"
    for name in names:
        constant = getattr(mt5, name, None)
        if constant is not None and value == constant:
            return name
    return f"UNKNOWN({_fmt(value)})"


def _flags_label(value: Any, flags: tuple[tuple[str, int], ...]) -> str:
    if value is None:
        return "-"
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return _fmt(value)
    labels: list[str] = []
    known_mask = 0
    for label, mask in flags:
        known_mask |= mask
        if numeric & mask:
            labels.append(label)
    remaining = numeric & ~known_mask
    if remaining:
        labels.append(f"OTHER(0x{remaining:x})")
    return ",".join(labels) if labels else "NONE"


def _load_mt5() -> tuple[Any | None, int]:
    if importlib.util.find_spec("MetaTrader5") is None:
        print("MetaTrader5 package: NOT INSTALLED", file=sys.stderr)
        print(
            "Install the diagnostic dependency after reviewing this message:",
            file=sys.stderr,
        )
        print(
            "  python -m pip install -r tools/requirements-mt5-probe.txt",
            file=sys.stderr,
        )
        print(
            "This probe does not install packages, change terminal settings, "
            "or request credentials.",
            file=sys.stderr,
        )
        return None, EXIT_PACKAGE_MISSING

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except Exception as exc:
        print(f"MetaTrader5 import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None, EXIT_PACKAGE_MISSING

    return mt5, EXIT_OK


def _resolve_terminal_path(cli_value: str | None) -> Path | None:
    if cli_value:
        return Path(cli_value)
    env_value = os.environ.get("MT5_TERMINAL_PATH")
    if env_value:
        return Path(env_value)
    for candidate in DEFAULT_TERMINAL_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _terminal_process_state() -> tuple[bool | None, str]:
    try:
        completed = subprocess.run(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq terminal64.exe",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
    return "terminal64.exe" in output.lower(), output


def _wait_for_terminal_connection(mt5: Any) -> Any:
    info = None
    for attempt in range(6):
        info = mt5.terminal_info()
        if info is not None and bool(_field(info, "connected")):
            return info
        if attempt < 5:
            time.sleep(1)
    return info


def _looks_gold(info: Any) -> bool:
    name = str(_field(info, "name") or "").upper()
    if name.startswith(GOLD_SYMBOL_PREFIXES):
        return True
    text = " ".join(
        str(_field(info, field_name) or "")
        for field_name in ("name", "description", "path", "exchange")
    ).upper()
    return any(token in text for token in GOLD_TEXT_TOKENS)


def _print_environment(
    mt5: Any,
    terminal_path: Path | None,
    process_running: bool | None,
) -> None:
    print("\n=== Environment ===")
    print(f"platform={sys.platform}")
    print(f"python={platform.python_version()}")
    print(f"python_bits={struct.calcsize('P') * 8}")
    print(f"MetaTrader5_version={_fmt(_field(mt5, '__version__'))}")
    print(f"terminal_path={_fmt(None if terminal_path is None else str(terminal_path))}")
    if process_running is None:
        print("terminal_process_running=unknown")
    else:
        print(f"terminal_process_running={_fmt(process_running)}")


def _print_terminal(mt5: Any, info: Any) -> None:
    print("\n=== Terminal ===")
    print(f"connected={_fmt(_field(info, 'connected'))}")
    print(f"trade_allowed={_fmt(_field(info, 'trade_allowed'))}")
    print(f"tradeapi_disabled={_fmt(_field(info, 'tradeapi_disabled'))}")
    print(f"build={_fmt(_field(info, 'build'))}")
    print(f"name={_fmt(_field(info, 'name'))}")
    print(f"company={_fmt(_field(info, 'company'))}")
    print(f"path={_fmt(_field(info, 'path'))}")
    print(f"data_path={_fmt(_field(info, 'data_path'))}")


def _print_account(mt5: Any, info: Any) -> None:
    trade_mode = _field(info, "trade_mode")
    margin_mode = _field(info, "margin_mode")

    print("\n=== Account ===")
    print(f"account_login_masked={_mask_login(_field(info, 'login'))}")
    print(
        "trade_mode="
        f"{_fmt(trade_mode)} "
        f"({_enum_label(mt5, trade_mode, ('ACCOUNT_TRADE_MODE_DEMO', 'ACCOUNT_TRADE_MODE_CONTEST', 'ACCOUNT_TRADE_MODE_REAL'))})"
    )
    print(f"company={_fmt(_field(info, 'company'))}")
    print(f"server={_fmt(_field(info, 'server'))}")
    print(f"currency={_fmt(_field(info, 'currency'))}")
    print(f"leverage={_fmt(_field(info, 'leverage'))}")
    print(
        "margin_mode="
        f"{_fmt(margin_mode)} "
        f"({_enum_label(mt5, margin_mode, ('ACCOUNT_MARGIN_MODE_RETAIL_NETTING', 'ACCOUNT_MARGIN_MODE_EXCHANGE', 'ACCOUNT_MARGIN_MODE_RETAIL_HEDGING'))})"
    )
    print(f"trade_allowed={_fmt(_field(info, 'trade_allowed'))}")
    print(f"trade_expert={_fmt(_field(info, 'trade_expert'))}")
    print(f"fifo_close={_fmt(_field(info, 'fifo_close'))}")
    print(f"limit_orders={_fmt(_field(info, 'limit_orders'))}")

    if getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None) == trade_mode:
        print("account_assessment=DEMO (reported by terminal)")
    elif getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", None) == trade_mode:
        print("account_assessment=REAL (reported by terminal)")
        print("warning=REAL account reported; this probe remains read-only.")
    elif getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", None) == trade_mode:
        print("account_assessment=CONTEST (reported by terminal)")
    else:
        print("account_assessment=UNKNOWN (terminal did not report a known mode)")


def _print_symbol_universe(
    mt5: Any,
    all_symbols: tuple[Any, ...],
    reported_total: Any,
    requested_symbols: list[str],
    list_all: bool,
    sample_other: int,
) -> tuple[list[Any], list[str]]:
    failures: list[str] = []
    by_name: dict[str, Any] = {}
    for info in all_symbols:
        name = str(_field(info, "name") or "")
        if name:
            by_name[name] = info

    candidate_names: list[str] = []
    if requested_symbols:
        for name in requested_symbols:
            exact = str(name).strip()
            if not exact:
                continue
            if exact in by_name:
                candidate_names.append(exact)
                continue
            info = mt5.symbol_info(exact)
            if info is None:
                failures.append(
                    f"symbol_info({exact!r}) returned None; {_error_text(mt5)}"
                )
                continue
            by_name[exact] = info
            candidate_names.append(exact)
    else:
        candidate_names = [
            name for name, info in by_name.items() if _looks_gold(info)
        ]

    candidate_names = list(dict.fromkeys(candidate_names))

    print("\n=== Symbols ===")
    print(f"symbols_total_reported={_fmt(reported_total)}")
    print(f"symbols_total={len(all_symbols)}")
    print(f"gold_candidates={len(candidate_names)}")
    if candidate_names:
        for name in candidate_names:
            info = by_name[name]
            print(
                f"- {name} | description={_fmt(_field(info, 'description'))} "
                f"| exchange={_fmt(_field(info, 'exchange'))} "
                f"| path={_fmt(_field(info, 'path'))}"
            )
    else:
        print("- none detected by name/description/path; use --symbol to inspect an exact name")

    if list_all:
        print("\nall_symbols:")
        for name in sorted(by_name):
            print(name)

    candidate_set = set(candidate_names)
    other_names = [
        name for name in sorted(by_name) if name not in candidate_set
    ]
    print(f"other_symbols_total={len(other_names)}")
    if other_names and sample_other > 0:
        print("other_symbols_sample:")
        for name in other_names[:sample_other]:
            info = by_name[name]
            print(
                f"- {name} | description={_fmt(_field(info, 'description'))} "
                f"| path={_fmt(_field(info, 'path'))}"
            )

    return [by_name[name] for name in candidate_names], failures


def _print_instrument(mt5: Any, name: str, info: Any) -> None:
    trade_mode = _field(info, "trade_mode")
    execution_mode = _field(info, "trade_exemode")
    calc_mode = _field(info, "trade_calc_mode")
    filling_mode = _field(info, "filling_mode")
    order_mode = _field(info, "order_mode")
    expiration_mode = _field(info, "expiration_mode")

    print(f"\n--- Instrument: {name} ---")
    print(f"name={_fmt(_field(info, 'name'))}")
    print(f"description={_fmt(_field(info, 'description'))}")
    print(f"exchange={_fmt(_field(info, 'exchange'))}")
    print(f"path={_fmt(_field(info, 'path'))}")
    print(f"currency_base={_fmt(_field(info, 'currency_base'))}")
    print(f"currency_profit={_fmt(_field(info, 'currency_profit'))}")
    print(f"currency_margin={_fmt(_field(info, 'currency_margin'))}")
    print(f"contract_size={_fmt(_field(info, 'trade_contract_size'))}")
    print(f"tick_size={_fmt(_field(info, 'trade_tick_size'))}")
    print(f"tick_value={_fmt(_field(info, 'trade_tick_value'))}")
    print(f"point={_fmt(_field(info, 'point'))}")
    print(f"digits={_fmt(_field(info, 'digits'))}")
    print(f"spread_points={_fmt(_field(info, 'spread'))}")
    print(f"volume_min={_fmt(_field(info, 'volume_min'))}")
    print(f"volume_max={_fmt(_field(info, 'volume_max'))}")
    print(f"volume_step={_fmt(_field(info, 'volume_step'))}")
    print(f"margin_initial={_fmt(_field(info, 'margin_initial'))}")
    print(f"margin_maintenance={_fmt(_field(info, 'margin_maintenance'))}")
    print(f"start_time={_fmt_epoch(_field(info, 'start_time'))}")
    print(f"expiration_time={_fmt_epoch(_field(info, 'expiration_time'))}")
    print(
        "trade_mode="
        f"{_fmt(trade_mode)} "
        f"({_enum_label(mt5, trade_mode, ('SYMBOL_TRADE_MODE_DISABLED', 'SYMBOL_TRADE_MODE_LONGONLY', 'SYMBOL_TRADE_MODE_SHORTONLY', 'SYMBOL_TRADE_MODE_CLOSEONLY', 'SYMBOL_TRADE_MODE_FULL'))})"
    )
    print(
        "execution_mode="
        f"{_fmt(execution_mode)} "
        f"({_enum_label(mt5, execution_mode, ('SYMBOL_TRADE_EXECUTION_REQUEST', 'SYMBOL_TRADE_EXECUTION_INSTANT', 'SYMBOL_TRADE_EXECUTION_MARKET', 'SYMBOL_TRADE_EXECUTION_EXCHANGE'))})"
    )
    print(
        "calc_mode="
        f"{_fmt(calc_mode)} "
        f"({_enum_label(mt5, calc_mode, ('SYMBOL_CALC_MODE_FOREX', 'SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE', 'SYMBOL_CALC_MODE_FUTURES', 'SYMBOL_CALC_MODE_CFD', 'SYMBOL_CALC_MODE_CFDINDEX', 'SYMBOL_CALC_MODE_CFDLEVERAGE', 'SYMBOL_CALC_MODE_EXCH_STOCKS', 'SYMBOL_CALC_MODE_EXCH_FUTURES', 'SYMBOL_CALC_MODE_EXCH_FUTURES_FORTS', 'SYMBOL_CALC_MODE_EXCH_BONDS', 'SYMBOL_CALC_MODE_EXCH_STOCKS_MOEX', 'SYMBOL_CALC_MODE_EXCH_BONDS_MOEX', 'SYMBOL_CALC_MODE_SERV_COLLATERAL'))})"
    )
    print(
        f"filling_modes={_fmt(filling_mode)} "
        f"({_flags_label(filling_mode, (('FOK', 1), ('IOC', 2), ('BOC', 4)))})"
    )
    print(
        f"order_modes={_fmt(order_mode)} "
        f"({_flags_label(order_mode, (('MARKET', 1), ('LIMIT', 2), ('STOP', 4), ('STOP_LIMIT', 8), ('SL', 16), ('TP', 32), ('CLOSEBY', 64)))})"
    )
    print(
        f"expiration_modes={_fmt(expiration_mode)} "
        f"({_flags_label(expiration_mode, (('GTC', 1), ('DAY', 2), ('SPECIFIED', 4), ('SPECIFIED_DAY', 8)))})"
    )
    print(f"visible={_fmt(_field(info, 'visible'))}")
    print(f"select={_fmt(_field(info, 'select'))}")
    print(f"custom={_fmt(_field(info, 'custom'))}")
    print("product_identity=UNVERIFIED")
    print(
        "identity_note=Broker metadata only. A GC/MGC name does not prove a "
        "COMEX futures contract; confirm exchange, contract specification, "
        "expiry, and settlement with the broker."
    )
    if "XAU" in name.upper():
        print(
            "instrument_note=XAUUSD is not treated as equivalent to COMEX "
            "GC/MGC futures and does not use Trantor's GC/MGC calendar."
        )


def _print_tick(mt5: Any, name: str, failures: list[str]) -> None:
    tick = mt5.symbol_info_tick(name)
    if tick is None:
        failures.append(f"symbol_info_tick({name!r}) returned None; {_error_text(mt5)}")
        print(f"latest_tick_{name}=unavailable")
        return
    print(
        f"latest_tick_{name}: "
        f"time={_fmt_epoch(_field(tick, 'time'))} "
        f"bid={_fmt(_field(tick, 'bid'))} "
        f"ask={_fmt(_field(tick, 'ask'))} "
        f"last={_fmt(_field(tick, 'last'))} "
        f"volume={_fmt(_field(tick, 'volume'))} "
        f"volume_real={_fmt(_field(tick, 'volume_real'))} "
        f"flags={_fmt(_field(tick, 'flags'))}"
    )


def _print_candles(mt5: Any, name: str, bars: int, failures: list[str]) -> None:
    print(f"\ncandles_{name}:")
    for label, timeframe in (
        ("D1", getattr(mt5, "TIMEFRAME_D1", None)),
        ("H1", getattr(mt5, "TIMEFRAME_H1", None)),
    ):
        if timeframe is None:
            continue
        rates = mt5.copy_rates_from_pos(name, timeframe, 0, bars)
        if rates is None:
            failures.append(
                f"copy_rates_from_pos({name!r}, {label}) returned None; "
                f"{_error_text(mt5)}"
            )
            continue
        try:
            count = len(rates)
        except TypeError:
            count = 0
        if count == 0:
            continue
        print(f"timeframe={label} count={count}")
        for record in rates:
            print(
                "bar: "
                f"time={_fmt_epoch(_record_field(record, 'time'))} "
                f"open={_fmt(_record_field(record, 'open'))} "
                f"high={_fmt(_record_field(record, 'high'))} "
                f"low={_fmt(_record_field(record, 'low'))} "
                f"close={_fmt(_record_field(record, 'close'))} "
                f"tick_volume={_fmt(_record_field(record, 'tick_volume'))} "
                f"spread={_fmt(_record_field(record, 'spread'))} "
                f"real_volume={_fmt(_record_field(record, 'real_volume'))}"
            )
        return
    print("timeframe=unavailable count=0")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only MetaTrader 5 connection, account, symbol, and candle probe."
        )
    )
    parser.add_argument(
        "--terminal-path",
        default=None,
        help=(
            "Path to terminal64.exe. Defaults to MT5_TERMINAL_PATH or a known "
            "common install path."
        ),
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=15000,
        help="MT5 initialize() timeout in milliseconds (default: 15000).",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help=(
            "Allow initialize() to launch the terminal if it is not already "
            "running. Default: stop and ask the operator to start it."
        ),
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Exact symbol name to inspect; may be repeated.",
    )
    parser.add_argument(
        "--bars",
        type=int,
        default=5,
        help="Number of candles to sample per instrument (default: 5).",
    )
    parser.add_argument(
        "--list-all-symbols",
        action="store_true",
        help="Print every available symbol name.",
    )
    parser.add_argument(
        "--sample-other",
        type=int,
        default=10,
        help="Number of non-gold symbols to display (default: 10).",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.bars <= 100:
        parser.error("--bars must be between 1 and 100")
    if not 0 <= args.sample_other <= 100:
        parser.error("--sample-other must be between 0 and 100")
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    mt5, code = _load_mt5()
    if mt5 is None:
        return code

    terminal_path = _resolve_terminal_path(args.terminal_path)
    process_running, process_detail = _terminal_process_state()

    _print_environment(mt5, terminal_path, process_running)
    if process_detail and process_running is None:
        print(f"terminal_process_check_error={process_detail}")

    if terminal_path is not None and not terminal_path.exists():
        print(
            f"warning=terminal path does not exist: {terminal_path}",
            file=sys.stderr,
        )
        terminal_path = None

    if process_running is False and not args.launch:
        print(
            "ERROR: terminal64.exe is not running. Start the MT5 terminal and "
            "rerun, or pass --launch to let initialize() start it.",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT

    initialized = False
    try:
        if terminal_path is not None:
            initialized = bool(
                mt5.initialize(str(terminal_path), timeout=args.timeout_ms)
            )
        else:
            initialized = bool(mt5.initialize(timeout=args.timeout_ms))

        if not initialized:
            print(
                f"ERROR: initialize() failed; {_error_text(mt5)}",
                file=sys.stderr,
            )
            return EXIT_INITIALIZE

        terminal_info = _wait_for_terminal_connection(mt5)
        if terminal_info is None:
            print(
                f"ERROR: terminal_info() returned None; {_error_text(mt5)}",
                file=sys.stderr,
            )
            return EXIT_ACCOUNT
        _print_terminal(mt5, terminal_info)

        if not bool(_field(terminal_info, "connected")):
            print(
                "ERROR: MT5 terminal is not connected to a broker; stopping.",
                file=sys.stderr,
            )
            return EXIT_ACCOUNT

        account_info = mt5.account_info()
        if account_info is None:
            print(
                f"ERROR: account_info() returned None; {_error_text(mt5)}",
                file=sys.stderr,
            )
            return EXIT_ACCOUNT
        _print_account(mt5, account_info)

        symbols_total = mt5.symbols_total()
        all_symbols = mt5.symbols_get()
        if symbols_total is None or all_symbols is None:
            print(
                f"ERROR: symbol enumeration failed; {_error_text(mt5)}",
                file=sys.stderr,
            )
            return EXIT_SYMBOLS

        all_symbols_tuple = tuple(all_symbols)
        candidates, symbol_failures = _print_symbol_universe(
            mt5,
            all_symbols_tuple,
            symbols_total,
            args.symbol,
            args.list_all_symbols,
            args.sample_other,
        )

        failures: list[str] = list(symbol_failures)
        for info in candidates:
            name = str(_field(info, "name") or "")
            if not name:
                continue
            _print_instrument(mt5, name, info)
            _print_tick(mt5, name, failures)
            _print_candles(mt5, name, args.bars, failures)

        print("\n=== Failures ===")
        if failures:
            for failure in failures:
                print(f"- {failure}")
        else:
            print("none")

        print("\n=== Read-only confirmation ===")
        print(
            "No trading, order, position, deal-history, or Market Watch "
            "mutation function was called."
        )
        print(
            "The probe did not call login(), did not request credentials, and "
            "did not change terminal security or automated-trading settings."
        )
        return EXIT_OK
    finally:
        if initialized:
            try:
                mt5.shutdown()
                print("\nshutdown=completed")
            except Exception as exc:  # pragma: no cover - shutdown fallback
                print(
                    f"\nshutdown=failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
