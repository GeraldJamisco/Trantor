# Testing Report — Historical Replay, Unit, Integration, Fault Injection, Performance, Security

- **Date:** 2026-11-04
- **Environment:** Windows 11, Python 3.13.14, MetaTrader5 5.0.6231, Node 24 (JS syntax check only)
- **Result:** `python -m pytest` → **637 passed, 0 failed** (481 pre-existing + 156 new)
- Tests need no broker, network or terminal. Real-data replay tests skip when
  `research_data/` is absent; immutability tests skip when `decisions.jsonl` is an
  un-checked-out LFS pointer.

## 1. Historical replay through the complete runtime (deliverable E)

The real `TrantorRuntime` (MT5-shaped source → market monitor → completed-bar
identification → strategy → canonical record → gate → broker → position manager
→ SQLite) was driven over the validated datasets by a simulated clock. The
replay source reveals only the open of the forming bar, so no future high, low,
close or volume is visible.

| Check | Window | Result |
|---|---|---|
| Runtime vs fresh backtest, **approved** strategy | 2024-01-01 → 2024-03-31 | 5,775 common decisions, **0 record-hash mismatches**; 0 trades in both. The single backtest-only decision (2024-03-29 00:00) precedes the Good Friday closure; the runtime evaluates a bar only once the next bar opens, which was outside the window. |
| Runtime vs **immutable Phase E baseline**, research variant | 2024-01-01 → 2024-03-28 | **5,775/5,775 record hashes identical; 3/3 trades identical** (entries, exits, realized R: +2.5R, −1R, −1R) |
| Same, with a forced shutdown/restart every 97 bars | same | **59 restarts**, still 5,775/5,775 hashes and 3/3 trades identical |
| Restart every 7 bars (test suite) | 2024-01-10 → 2024-01-11 | positions and hashes identical to an uninterrupted run |
| Re-run on the same state | same | 0 new evaluations, 0 duplicate positions |

What this establishes: no lookahead (identical decisions to the point-in-time
backtester under a stricter information set), correct timeframe alignment,
identical indicator values, rejection reasons, stops/targets, partial exits,
breakeven, cooldown and concurrency behaviour, and restart safety. Intentional
differences: the runtime's paper equity model can evaluate the 3% daily-loss
rule (the backtest cannot); the replay used the `neutral` model for equality.

## 2. Unit tests (new modules)

| File | Tests | Covers |
|---|---:|---|
| `test_runtime_market_data.py` | 29 | clock-offset measurement, rounding, 3-sample verification, DST offset change, stale-tick rejection; completed vs forming bars, bar exactly at close, broker-clock skew (observed live), duplicates, out-of-order, off-grid, NaN/inf/negative/invalid timestamps, missing-bar gaps, weekend labelling, future bars, DST-week D1 grids; feed monitor live/stale/disconnected/crossed quotes/rates failure; MT5 gateway backoff, reconnect, disconnect detection, exception handling, login masking, absence of order functions |
| `test_runtime_state.py` | 35 | SQLite WAL, idempotent decisions, duplicate `client_order_id`, rollback, optimistic version conflict, read-only refusal, corrupt state, newer-schema refusal, notification dedup; risk state creation/persistence/mismatch/corruption, 16/32-bar cooldowns, 3% lockout and UTC-day reset, unrealized loss, neutral model; sizing (blocked without economics, round-down to even steps, minimum volume over risk, commission/margin unknown, ceilings, invalid inputs, max volume cap, broker cross-check); calendar (unavailable, missing, malformed, unconfirmed, stale, coverage gap, DST local times, event windows, duplicates, unknown event types, naive times); broker verification (observed vs verified, owner confirmation, mismatches, USD-base CFD convention) |
| `test_execution_gate_and_notify.py` | 25 | gate per mode (disabled/research/demo/live refused), adapter and live flag, every limit re-checked, stricter settings win, kill switch lifecycle and corrupt file; paper broker fill, idempotency, rejection, no external I/O; notifier dedup, redaction, failure isolation, queue overflow, background delivery; Telegram transport never leaks the token |
| `test_research_experiments.py` | 7 | server→UTC conversion (winter, summer, US-on/EU-off week, live measurement), CME break mapping, no-overwrite policy, converted series monotonic |
| `test_baseline_immutability.py` | 16 | SHA-256 of all Phase E/F artifacts and the new Phase G artifacts; no extra baseline files |
| `test_mt5_demo_adapter.py` | 8 | demo-only guard, idempotent resubmission, deal-history lookup after restart, no retry after unknown outcome, partial fills, requote and order-check rejection, stops level, never-widen stop modification, partial close, reconciliation, adapter not constructed by the runtime |

The pre-existing 481 tests (strategy indicators, EMA/RSI/ATR, pivots, zones,
Fibonacci, patterns, tick volume, scoring, stops/targets, research records,
backtester, safety, config) all still pass unchanged.

## 3. Integration tests

`test_runtime_integration.py` (17) and `test_dashboard_live.py` (19):
shadow replay vs baseline; paper replay through gate → paper broker → orders →
order events → positions → daily risk → reconciliation; restart during an open
position; duplicate prevention; emergency stop with and without open positions;
late evaluation; approved-strategy identity in records; backend ↔ dashboard
endpoints; Telegram ↔ notifier (fake transport).

## 4. Fault injection

| Fault | How | Observed behaviour |
|---|---|---|
| MT5 disconnect | test fake; **also happened live** 11:08 UTC | feed `disconnected`, quote withdrawn, no evaluations; live: two IPC timeouts, reconnected after ~37 s with backoff 2 s / 4 s |
| Application crash | `Stop-Process -Force` on the live shadow runtime | SQLite `integrity_check = ok`, lock released by OS, restart reconciled cleanly, decisions resumed on the next bar |
| Network/feed freeze, delayed ticks | frozen/lagged tick times | stale after 120 s → entries blocked; 40 s lag stays live |
| Missing / duplicate / out-of-order / invalid candles | crafted raw rows | dropped and counted, never repaired; quality issue blocks entries |
| Database failure | engine raises `StoreError` | entries halted, critical event |
| Risk-state corruption / mismatch | edited state | load fails closed; entries halted |
| Restart with open position | replay restarts | identical results; no duplicate exits |
| Duplicate execution requests | same decision twice; same `client_order_id` | second insert refused; paper broker idempotent |
| Telegram failure | transport raising | recorded as failed; loop unaffected |
| Calendar outage / malformed | missing/invalid file | `unavailable` / `verification_failed`; never "no news" |
| Invalid symbol | gateway with `GOLD` | connect refused |
| Invalid broker metadata | custom symbol, wrong tick value, wrong grid | `mismatch`; identity failure halts entries |
| Stale configuration / unsafe modes | demo/live/disabled settings | runtime refuses to start (exit 2) |
| Second instance | two runtimes on one state dir | `LockError` |
| Late evaluation | evaluated 5 min after entry bar | `entry window missed`, no entry |

Not fault-injected because the component does not exist: broker order
rejection, timeout-then-unknown-acknowledgement and partial fills at a real
broker (demo adapter not implemented). The paper broker's equivalents
(rejection without a price, non-market rejection, unknown outcome abandoned on
recovery, never retried) are covered.

## 5. Performance and stability

| Measurement | Result |
|---|---|
| Replay throughput (full history D1/H4, under concurrent load) | 14.6–15.2 ms per runtime step; Q1-2024 (5,776 steps) in 156 s |
| Memory, replay | 55 MB after loading 75k bars; +2 MB over 920 steps |
| Memory, live process | 15–45 MB working set |
| Live step time | ≈1 step/s; normal steps < 0.6 s; worst 33.6 s during an MT5 IPC-timeout reconnect |
| Persistence volume | ~5.6 KB per decision → ~134 MB per year of M15 decisions |
| Dashboard | status refresh every 3 s; decision history server-side limited (≤500 rows) |

## 6. Security

| Check | Result |
|---|---|
| Dashboard writes | POST/PUT/PATCH/DELETE → 405; runtime DB opened `mode=ro`; file mtime unchanged after every endpoint |
| Path traversal / injection | encoded `..`, SQL-like ids, oversized ids, unknown endpoints → 400/404; static files are a fixed whitelist |
| Network exposure | CLI refuses non-loopback bind (no authentication exists) |
| Order authorization bypass | no browser path to the gate; the gate re-derives mode, kill switch, reconciliation, limits; demo/live refused in code |
| Secret leakage | Telegram token redacted from messages, errors and stored notification errors; status snapshot contains no token and a masked account login |
| Credentials | runtime never logs in; MT5 credentials are never read |
| Logs | JSON-lines with `SecretRedactingFilter` |
