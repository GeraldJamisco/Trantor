"""Re-derive live shadow/paper decisions from broker history and compare hashes.

Read-only. For each recorded decision this tool re-fetches D1/H4/M15 bars from
the MT5 terminal, rebuilds exactly the windows the runtime used (from the audit
envelope: last bar times and bar counts), normalizes them with the recorded
broker offset, re-evaluates the same strategy with the same risk state and
compares the canonical record hash. A match shows the live decision is
reproducible from broker data; a mismatch is reported with its cause.

    $env:PYTHONPATH='src'
    python tools/reproduce_live_decisions.py --state-dir state --limit 50
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trantor.config import load_settings
from trantor.models import MarketObservation
from trantor.runtime.candles import analyse_candles
from trantor.runtime.mt5_gateway import MT5Gateway
from trantor.runtime.store import RuntimeStore
from trantor.strategy.research_record import canonical_json, serialize_strategy_decision
from trantor.strategy.xauusd_p import ResearchRiskState, XAUUSDPMultiTimeframeStrategy

UTC = timezone.utc


def _window(bars: tuple[MarketObservation, ...], last_open: str | None, count: int) -> tuple[MarketObservation, ...]:
    if last_open is None:
        return ()
    end = next((i for i, bar in enumerate(bars) if bar.observed_at.isoformat() == last_open), None)
    if end is None or end + 1 < count:
        raise LookupError(f"bar {last_open} with {count} bars of history not available")
    return bars[end + 1 - count : end + 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--settings", default=None)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    settings = load_settings(args.settings)
    store = RuntimeStore(Path(args.state_dir) / "trantor_runtime.sqlite3", read_only=True)
    rows = store.recent_decisions(args.limit)
    profile = rows[0]["profile"] if rows else "shadow"
    risk_raw = store.get_state(f"risk:{profile}")
    store.close()
    if not rows:
        print("no decisions recorded")
        return 1
    gateway = MT5Gateway(terminal_path=settings.mt5_terminal_path)
    strategy = XAUUSDPMultiTimeframeStrategy()
    results = {"match": 0, "mismatch": 0, "unavailable": 0}
    try:
        raw = {tf: gateway.rates(tf, n) for tf, n in (("1d", 2600), ("4h", 1200), ("15m", 2000))}
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            offset = timedelta(seconds=envelope["time"]["time_basis"]["offset_seconds"] or 0)
            server_now = datetime.now(UTC) + offset
            frames = {
                tf: analyse_candles(raw[tf], symbol="XAUUSD-P", timeframe=tf, server_now=server_now, shift=offset)
                for tf in raw
            }
            data = envelope["data"]
            decision_time = datetime.fromisoformat(row["decision_time"])
            try:
                d1 = _window(frames["1d"][0], data["last_d1_open"], data["d1_bars"])
                h4 = _window(frames["4h"][0], data["last_h4_open"], data["h4_bars"])
                m15 = _window(frames["15m"][0], data["trigger_m15_open"], data["m15_bars"])
            except LookupError as exc:
                results["unavailable"] += 1
                print(f"{row['decision_time']}  UNAVAILABLE  {exc}")
                continue
            next_open = data.get("next_bar_open")
            next_bar = None if next_open is None else MarketObservation(
                symbol="XAUUSD-P",
                timeframe="15m",
                observed_at=datetime.fromisoformat(data["next_bar_open_time"]),
                open=next_open,
                high=next_open,
                low=next_open,
                close=next_open,
            )
            risk = ResearchRiskState(
                equity=risk_raw["equity"],
                start_of_day_equity=risk_raw["start_of_day_equity"],
                open_positions=0,
                entries_today=0,
            )
            news_unaware = not envelope["news"]["verified_for_strategy"]
            decision = strategy.evaluate(
                d1, h4, m15,
                decision_time=decision_time,
                next_m15_bar=next_bar,
                risk_state=risk,
                news_context=None,
                research_mode=True,
                news_unaware=news_unaware,
                session_calendar_verified=False,
            )
            record_hash = hashlib.sha256(canonical_json(serialize_strategy_decision(decision, strategy.identity)).encode()).hexdigest()
            same = record_hash == row["record_hash"]
            results["match" if same else "mismatch"] += 1
            print(f"{row['decision_time']}  {'MATCH   ' if same else 'MISMATCH'}  {row['status']}  {json.loads(row['rejection_codes'])}")
    finally:
        gateway.close()
    print(json.dumps(results))
    return 0 if results["mismatch"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
