# Trantor Operator Guide (Windows)

Audience: the owner/operator running Trantor on the Windows development machine.
Status at writing (2026-11-04): shadow mode and paper mode are operational;
demo and live execution are **not authorized** and are refused in code.

---

## 1. What runs where

| Process | Command | Reads | Writes | Can send broker orders? |
|---|---|---|---|---|
| Runtime | `python -m trantor.runtime run` | MT5 terminal (read-only API), settings, calendar, broker expectations | `state/trantor_runtime.sqlite3`, `state/*.json`, `logs/<state dir>/trantor.jsonl` | **No.** Only `research` (shadow) and `paper` modes start. |
| Dashboard | `python -m trantor.dashboard` | Phase E/F artifacts, runtime DB (`mode=ro`) | nothing | No. GET only, loopback only. |
| Research | `python -m trantor.research.backtest`, `python -m trantor.research.experiments` | `research_data/` | a **new** output directory | No. |

The MT5 terminal (`C:\Program Files\MetaTrader 5\terminal64.exe`) must be logged
in to the Fortune Prime **demo** account. The runtime attaches to the running
terminal; it never logs in, never reads credentials and has no order function.

## 2. First-time setup

```powershell
cd C:\xampp\htdocs\Trantor
python -m pip install -r requirements.txt
python -m pip install MetaTrader5==5.0.6231
git lfs checkout   # restores the 175 MB Phase E decisions.jsonl from the local LFS cache
Copy-Item config\settings.example.json config\settings.json
```

Edit `config\settings.json` (git-ignored):

```json
{
  "schema_version": 1,
  "trading_mode": "paper",
  "execution_mode": "research",
  "live_trading_enabled": false,
  "broker_adapter": "paper",
  "max_risk_per_trade_fraction": 0.01,
  "max_concurrent_positions": 2,
  "max_daily_loss_fraction": 0.03,
  "log_level": "INFO",
  "market_data_provider": "mt5",
  "mt5_terminal_path": "C:/Program Files/MetaTrader 5/terminal64.exe"
}
```

- `execution_mode: "research"` = shadow mode (hypothetical positions, no orders).
- `execution_mode: "paper"` = shadow plus the in-process paper broker.
- Any other value makes `run` exit with code 2.
- Settings may be stricter than the strategy, never looser: the gate uses the
  minimum of settings, strategy ceilings (1% / 2 positions / 3%) and the hard
  safety ceilings.

Optional Telegram (`.env`, git-ignored):

```text
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_CHAT_ID=...
```

Without them notifications are recorded in the database as `disabled` and
nothing is sent.

## 3. Daily operation

```powershell
$env:PYTHONPATH='src'
python -m trantor.runtime verify-broker          # read-only broker check, writes state\broker_verification_*.json
python -m trantor.runtime run                    # foreground; Ctrl+C stops gracefully
python -m trantor.dashboard                      # second window; open http://127.0.0.1:8765/
python -m trantor.runtime status                 # last status snapshot as JSON
```

`run --duration-minutes N` stops after N minutes. A second `run` against the same
`state\` directory is refused (OS file lock). Use `--state-dir` to keep a separate
paper state, e.g. `--state-dir state_paper` with a paper settings file passed via
`--settings`.

### What the runtime does each second

1. Polls the MT5 tick; measures the broker clock (offset to UTC, see §6).
2. Every 5 s refreshes M15 bars (D1/H4 only when a bar can have completed, or every 5 min).
3. Manages open shadow/paper positions on every newly **completed** M15 bar
   (stop-first, breakeven after TP1, TP2 only after TP1).
4. When M15 bar `T` has completed and bar `T+1` has opened, evaluates the
   approved strategy once for `T` (duplicate evaluations are impossible: one row
   per strategy/profile/decision time), stores the canonical record and an audit
   envelope, and — only if every gate passes — opens a shadow/paper position at
   the `T+1` open.
5. Writes a status snapshot every 2 s; re-verifies broker metadata every 6 h.

An entry is never made when: the feed is not `live`, the broker clock is not
verified, candle quality issues exist, the latest M15 bar is stale, the
evaluation is more than 120 s after the entry bar opened, the emergency stop is
engaged, startup reconciliation failed, or any strategy/risk rule rejects.

## 4. Emergency stop

```powershell
python -m trantor.runtime stop-entries --reason "unexpected volatility"
python -m trantor.runtime resume-entries --confirm
```

- Takes effect on the next cycle, survives restarts (`state\EMERGENCY_STOP`).
- Blocks **new entries** and strategy-triggered orders only.
- **Does not** close positions, stop position monitoring, exit management or
  risk enforcement. No liquidation policy exists or is implied.
- The dashboard shows it but cannot change it.

To stop everything: stop new entries first, then Ctrl+C the runtime. Shutdown
never closes positions; open paper/shadow positions resume management on the
next start from the last processed bar.

## 5. Risk controls (all enforced; ceilings, not targets)

| Control | Value | Where |
|---|---|---|
| Risk per trade | ≤1.0% high confidence (score 7–8), ≤0.5% medium (5–6) | strategy + gate |
| Score floor | <5 → no trade; H4 direction mandatory regardless of score | strategy |
| Daily loss | 3% of paper equity (realized + unrealized) → no new entries until next 00:00 UTC | risk tracker + gate |
| Entries per day | 3 | strategy + gate |
| Concurrent positions | 2 | strategy + gate |
| Cooldown | 16 M15 bars after a loss, 32 after two consecutive losses | strategy (persisted state) |
| Duplicate entries | impossible: unique decision row, unique `client_order_id`, unique position per decision | database |
| Monetary sizing | **blocked** until broker economics are owner-confirmed | `runtime/risk.py` |

Paper P/L uses a declared model: `P/L = realized R × (risk fraction × paper
equity at entry)`. Volume rounding, spread, commission and swap are not
modelled; paper results are gross and labelled as such.

Missing or inconsistent risk state at startup (e.g. state says 1 open position,
store says 0) halts new entries and raises a critical event until the
inconsistency is resolved by the operator.

## 6. Time and data provenance

- MT5 times are broker **server time**. Measured 2026-11-04: server = UTC+2.
  Historical data shows server = New York + 7 h (UTC+3 during US DST).
- The runtime measures the offset continuously from fresh ticks (3 agreeing
  samples, 30-minute rounding) and normalizes all bars to UTC. Until verified,
  candles are labelled `UNVERIFIED CLOCK` and no entry is possible.
- The dashboard shows raw broker time, normalized UTC and your local time
  separately.
- Historical research datasets remain in `dataset_time` (= server time); the
  Phase G experiment converts them with the inferred rule and says so.

## 7. Owner actions that unlock blocked capabilities

| Capability | Blocked by | Owner action |
|---|---|---|
| News point and news pause | no owner-confirmed calendar | Check `config\news_calendar.json` against the BLS/Fed pages listed in it, then set `"owner_confirmed": true`, `"confirmed_by"`, `"confirmed_at"`. Re-confirm at least weekly (older confirmations are treated as stale). |
| Monetary sizing | economics observed but not confirmed | Compare `state\broker_expectations.proposed.json` with Fortune Prime's XAUUSD-P contract specification; copy it to `config\broker_expectations.json`, fill `source`, set `"confirmed_by_owner": true`. |
| Commission model | no commission evidence for new trades | Obtain the account's commission schedule. |
| Broker session calendar | not exposed by the MT5 Python API | Obtain Fortune Prime's XAUUSD-P trading-hours/holiday schedule. |
| Demo execution | not implemented; not authorized | Separate written authorization, then a demo adapter, its validation and a demo report (see acceptance report). |
| Live execution | not authorized | Separate readiness report and explicit approval after demo. |

## 8. Notifications

Sent (deduplicated, one-way): startup, shutdown, market-data disconnect and
reconnect, every strategy candidate with its outcome, paper/shadow position
opened, each partial/final exit, daily-loss lockout, entries halted (critical),
daily summary at the UTC day change. A Telegram failure is recorded and never
affects trading logic.

## 9. Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `feed disconnected` | terminal lost the broker or IPC | Runtime reconnects with backoff (1 s → 60 s). Check the terminal. |
| `clock_unverified` | fewer than 3 fresh ticks agree | Normal for a few seconds at start; persistent → market closed or feed frozen. |
| `stale` | no new tick for >120 s | Market closed (weekend/daily break 22:00–23:00 UTC in winter) or feed problem. |
| `entries halted: reconciliation failed` | orders/positions/risk disagree | Inspect `python -m trantor.runtime status` and the Operations page; do not delete the database. |
| `another Trantor runtime is using state` | lock held | Another runtime is running against that directory. |
| Dashboard shows `RUNTIME NOT RUNNING` | status older than 30 s | Start the runtime; no prices are shown while it is stopped. |

## 10. Files

```text
config/settings.json                 machine settings (git-ignored)
config/news_calendar.json            official-source calendar, owner_confirmed=false
config/broker_expectations.json      owner-confirmed economics (absent until the owner creates it)
state/trantor_runtime.sqlite3        decisions, orders, positions, risk, health, notifications
state/EMERGENCY_STOP                 present = entries blocked
state/broker_verification_*.json     verification reports
state/broker_expectations.proposed.json  template for the owner
logs/<state dir>/trantor.jsonl       structured logs per runtime (rotating, secrets redacted)
```
