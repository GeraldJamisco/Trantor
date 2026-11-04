# Trantor Completion Programme — Implementation and Testing Log

This log records what was found, what was built, what was tested, and what
remains blocked during the full-system completion programme that started on
2026-11-04. Entries are appended in order. Nothing here changes the approved
strategy rules or the Phase E/F baselines.

## Entry 1 — 2026-11-04 — Repository inspection and baseline

### Baseline evidence

- `git status`: clean at `99b5bef`.
- `research_results/xauusd_p/phase_e_authoritative_20261102/decisions.jsonl`
  was a Git LFS pointer in the working tree. The 174,573,345-byte object was
  present in the local LFS cache and was restored with
  `git lfs checkout` (no content change; `git status` stayed clean).
- Test suite before restoration: 476 passed, 5 failed (all five read the LFS
  pointer). After restoration: **481 passed**.
- SHA-256 of the immutable baselines (guarded by
  `tests/test_baseline_immutability.py`):

| Artifact | SHA-256 |
|---|---|
| phase_e .../decisions.jsonl | `0604640d471eb51520ccf0a3e0cc8c3d43e7a23d2c1150b6e25b0eece885b0e5` |
| phase_e .../entry_attempts.json | `8add875162ce5a19940c60c5401555319363d56e2553a263c047038841f6c814` |
| phase_e .../manifest.json | `7e6e40e30cf8b1005ae5cb56dfffea774a243c379bc9937fb8fccc1dbfb0eea4` |
| phase_e .../summary.json | `8d8e1fc48fee84d8ad1cd605ea911f4fd156fbcb82ee04ff6087a58e376c9e74` |
| phase_e .../trades.json | `6885715d81eac80e7cc2d9a9a092a5069cd917c2dc4e672b42ac6e19b5dc4acd` |
| phase_f .../breakdowns.json | `221174d6ab5e19e4ba99ca1e2b1a219131ef18ef0be9fb645d6ff98b9b99069d` |
| phase_f .../summary.json | `fc1a10e85ea69cba51d1cf22f15c7a7aefb94663fe2d5216d3d78878bafc7bbd` |
| phase_f .../trade_diagnostics.csv | `60cddd88669658ad6901f5852ff751929fdaf42c13d8b892e762b1d7e173462d` |
| phase_f .../trade_diagnostics.json | `b139cef8ca5784e5b0935a5d4f1d9b81f291b6f25c23731e6f8ec1e38f7c418c` |

### Implementation inventory (state at inspection)

Legend: **C** complete and verified · **I** implemented, requires integration ·
**P** incomplete · **B** blocked by missing external information · **N** not
implemented.

| Subsystem | State | Evidence / gap |
|---|---|---|
| Approved strategy rules (D1 EMA bias, H4 pivots/trend, S/R + Fibonacci zones, M15 RSI/engulfing/pin/tick volume, 8-point score, stop, TP1/TP2/obstacle, R risk gates) | C | `strategy/xauusd_p.py`, 2,091-line test module; identity `xauusd_p_approved_v1` |
| Research-isolated variant `xauusd_p_filter_isolated_v1` | C | `strategy/xauusd_p_research.py` |
| Canonical research-record contract v1.0 | C | `strategy/research_record.py` |
| Historical datasets (D1/H4/M15 + D1 warm-up) with checksums | C | `research_data/xauusd_p/runs/*` (git-ignored, present locally) |
| Gross backtester, splits, linkage validation | C | `research/backtest/*` |
| Phase E authoritative results / Phase F diagnostics | C | immutable baselines above |
| Instrument profile + fail-closed identity | C | `instruments.py`; economics explicitly unverified |
| Strict configuration, execution-mode enum (disabled/research/paper/demo/live) | C | `config.py`; default `disabled` |
| Paper-only safety gate, secret redaction | C | `safety.py`; demo/live rejected |
| Read-only MT5 OHLC provider | I | `market_data/mt5_provider.py`; no ticks, bid/ask, completed-bar flag, reconnection, clock-offset handling |
| MT5 timestamp semantics (server time vs UTC) | B | Task 1: UNVERIFIED; measurable with a live terminal |
| Research dashboard (read-only) | I | research views only; no live/paper/risk/health views |
| Live market-data service / monitor | N | — |
| Broker-metadata verification process | N | only a static snapshot in docs |
| Economic calendar integration | N/B | no verified source in repository |
| Broker XAUUSD-P session calendar | B | MT5 Python API does not expose session times |
| Persistence and restart recovery | N | no database |
| Persistent risk tracker (daily/cooldown/concurrency) | P | backtest-local `_RiskBook` only |
| Monetary position sizing | N/B | blocked until economics verified |
| Paper broker / order lifecycle / position manager | N | `DisabledBroker` only |
| Decision engine (live pipeline) | N | — |
| Scheduler, locking, emergency stop | N | — |
| Telegram notifications | N | `.env.example` placeholders only |
| Structured logging | P | redaction filter exists; no logging setup |
| Demo execution adapter | N | must stay gated |
| Live execution | N | not authorized |

### Interpretation decisions taken in this programme

These are engineering decisions, not strategy changes. Each is recorded so the
owner can reverse it.

1. **Live strategy identity.** The live/shadow/paper pipeline evaluates the
   approved strategy (`xauusd_p_approved_v1`), not the research-isolated
   variant. Phase E results are never attributed to it.
2. **Paper entries and the news filter.** The specification blocks
   production/demo entries without a verified calendar. Paper is a simulation,
   so when no verified calendar is available paper runs the approved strategy in
   its labelled `news_unaware` research mode: no news point, no news pause, and
   every record carries the news-unaware limitation. Demo/live always require a
   verified calendar.
3. **Paper fill model.** Paper trading uses the exact conservative bar model of
   the backtester (`research/backtest/fills.py`): entry at the next M15 open,
   stop-first on ambiguous bars, gap-through-stop at the open, targets at the
   target price, one target per bar, breakeven after TP1. Paper is therefore a
   forward test of the backtest model on live bars, not a tick simulation.
4. **Paper equity model.** Paper P/L is `realized R × risk amount`, where risk
   amount is the confidence-tier risk fraction of paper equity at entry. This
   makes the 3% daily-loss rule evaluable in paper without claiming contract
   economics. Volume rounding and costs are not modelled and are labelled.
5. **Late evaluation.** If the decision for M15 bar `T` is evaluated more than
   120 seconds after bar `T+1` opened (for example after a restart), the decision
   is recorded but no entry is made. Implemented with a 120-second window
   (`entry window missed` in the blocked reasons).

## Entry 2 — 2026-11-04 — Runtime implementation

Built (all new; no strategy rule, dataset or baseline changed):

| Module | Purpose |
|---|---|
| `runtime/timebase.py` | broker clock offset measured from fresh ticks; raw vs normalized time provenance |
| `runtime/candles.py` | completed vs forming bars, duplicates, out-of-order, off-grid, invalid rows, gaps |
| `runtime/mt5_gateway.py` | serialised read-only MT5 access, health, reconnect with backoff, masked account |
| `runtime/market.py` | feed monitor: freshness, quote, spread stats, quality, entry blockers |
| `runtime/broker_verification.py` | explicit verification (observed / verified / mismatch) with broker calc cross-checks |
| `runtime/calendar.py` | news calendar with five distinct states; never "no news" from missing data |
| `runtime/sessions.py` | IANA London/NY windows for operators; broker calendar marked unverified |
| `runtime/risk.py` | persisted risk tracker (daily/cooldown/lockout/concurrency) and monetary sizing |
| `runtime/positions.py` | shadow/paper positions managed with the backtest fill function |
| `runtime/engine.py` | decision pipeline, audit envelope around the unchanged canonical record, reconciliation |
| `runtime/service.py` | scheduler, process lock, recovery, health, verification, daily summary, shutdown |
| `runtime/replay.py` | historical replay of the whole runtime with a simulated clock |
| `execution/gate.py` | XAUUSD-P execution gate and file-backed emergency stop |
| `execution/paper.py` | in-process paper broker (idempotent, no external I/O) |
| `execution/mt5_demo.py` | demo adapter — implemented and tested against a fake; not wired, not authorized |
| `monitoring/notify.py`, `monitoring/logs.py` | Telegram notifier (queued, deduplicated, redacted), JSON logs |
| `dashboard/runtime_data.py`, `static/live.js` | five live views, live strip, strategy comparison; read-only |
| `research/experiments.py` | Phase G experiment (approved strategy, UTC via inferred broker rule) |

## Entry 3 — 2026-11-04 — Findings and defects found during testing

1. **Broker server clock = UTC+2** (live), and historical data shows server = New
   York + 7 h. Phase E `dataset_time` is server time (L-01 answered).
2. **Defect (fixed):** at an M15 boundary the broker clock leads host+offset by
   ~0.5 s; the new bar looked like a future bar and marked candle quality dirty,
   which would have blocked an entry. Fix: server clock estimate = max(host +
   offset, newest tick), 5 s tolerance, and every bar followed by a newer bar is
   complete. Regression tests added.
3. **Defect (fixed):** broker reports XAUUSD-P base currency as USD; the identity
   check assumed XAU. Now accepts a gold description under a metals path and says so.
4. **Defect (fixed):** live candle endpoint labelled cached candles "LIVE DATA"
   while the feed was disconnected. Now "LAST RECEIVED - FEED DISCONNECTED".
5. **Defect (fixed):** Windows memory metric always `None` (untyped handle in ctypes).
6. **Defect (fixed):** D1/H4 were re-fetched on every replay step; refresh is now
   scheduled by bar completion with a 5-minute safety refresh.
7. **Real disconnect observed** 11:08 UTC; recovered automatically in ~37 s.
8. **Hard-kill crash test** on the live runtime: database intact, clean restart.

## Entry 4 — 2026-11-04 — Verification runs

- Full suite: 637 passed (481 pre-existing + 156 new), including demo-adapter and Phase G immutability tests.
- Runtime replay vs Phase E baseline (Q1 2024): 5,775/5,775 hashes, 3/3 trades,
  also with 59 restarts. Details: `testing_report.md`.
- Live: `verify-broker` on the demo terminal; shadow runtime continuously from
  10:58 UTC; 20-minute live paper mechanics run in parallel.

## Entry 5 — 2026-11-04 — Live window results and late fixes

- Live shadow and paper ran through the London/New York overlap: 46 decisions,
  100% bar coverage, all 46 reproduced from broker history with identical
  hashes, 3 real broker disconnects recovered automatically, 0 step errors.
  All decisions were `d1_conflict`; no candidate occurred.
- **Defect (fixed):** decisions rejected before the session step were shown as
  "outside session" (the strategy's "not evaluated" placeholder carries
  `in_overlap=False`). The envelope now records `evaluated`/`iana_overlap=None`,
  and the dashboard shows "not evaluated". Canonical records are unchanged.
  Regression test added.
- **Change:** each runtime now logs to `logs/<state dir>/` so two runtimes never
  rotate the same file.
- **Watch item (L-20):** working-set memory grew 15→23 MB (shadow) and 16→30 MB
  (paper) over ~5 h; whether it plateaus must be confirmed in the multi-day run.
- Step 2 (M15 history extension) prepared, not started, per owner decision:
  `docs/strategy/step2_history_extension_plan.md`.
