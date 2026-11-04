# Trantor — Final System Acceptance Report

- **Date:** 2026-11-04
- **Scope:** completion programme for the XAUUSD-P system (Fortune Prime MT5)
- **Verdict:** Trantor is **operational in shadow and paper mode on live demo-feed
  data**, with the approved strategy, canonical records, persistent risk
  controls, emergency stop, monitoring and dashboard. It is **not** ready for
  demo or live execution, and **no profitability claim is supported**.
  Acceptance stages 14–16 below are incomplete or not authorized, and four
  external facts still need the owner.

Companion documents: `operator_guide.md`, `testing_report.md`,
`broker_verification_report.md`, `shadow_and_paper_report.md`,
`implementation_log.md`, `../strategy/phase_g_approved_strategy_utc.md`.

---

## 1. What Trantor can do today

1. Attach read-only to the running MT5 demo terminal; verify account type,
   symbol identity and contract economics against the broker's own calculators.
2. Measure the broker clock (UTC+2 today) and normalize every bar to UTC with
   explicit provenance (raw broker / normalized / host / display).
3. Identify completed vs forming D1/H4/M15 candles; detect missing, duplicate,
   out-of-order, off-grid and invalid candles; report freshness; reconnect.
4. Evaluate the approved strategy `xauusd_p_approved_v1` exactly once per
   completed M15 bar (D1 EMA50/200 bias, H4 structure/zones/Fibonacci, M15
   RSI/patterns/tick volume, 8-point score, stops, TP1/TP2, R-risk gates).
5. Store every decision (accepted and rejected) as the unchanged canonical
   record plus an audit envelope: strategy identity, parameter and config
   fingerprints, data identity, time provenance, news/session status, blockers,
   outcome, record hash.
6. Run shadow (hypothetical) or paper (in-process broker) positions: next-bar-open
   entry, stop-first exits, 50/50 partials, breakeven after TP1, cooldowns, daily
   limits, concurrency — persisted, reconciled and restart-safe.
7. Refuse any order outside paper; block entries on stale/disconnected data,
   unverified clock, candle issues, late evaluation, reconciliation failure,
   storage failure or emergency stop — while continuing to manage open positions.
8. Notify via Telegram (when credentials are provided), deduplicated and isolated.
9. Show all of it on a read-only local dashboard with provenance labels.
10. Replay history through the whole runtime and run versioned research experiments.

## 2. Stage-by-stage acceptance (mandate §44)

Legend: **Implemented** (code exists) · **Tested** (automated tests) · **Verified**
(checked against real data/broker) · **Operational** (running live) ·
**Blocked** (external fact missing) · **Not authorized**.

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | All approved strategy components | Implemented, Tested, Verified (historical), Operational (live). News component implemented but **Blocked** (calendar not owner-confirmed) | strategy tests; Phase E equivalence; live decisions |
| 2 | Subsystems integrated | Implemented, Tested, Operational. Demo adapter implemented, intentionally not wired | `runtime/`, integration tests |
| 3 | Canonical decision records | Operational; record contract unchanged | 5,775/5,775 hash equality vs baseline; live reproducibility (§4) |
| 4 | Risk management | R-based controls Operational; monetary sizing Implemented+Tested, **Blocked** | `runtime/risk.py`, tests |
| 5 | Market-data integration | Operational, Verified on live demo feed | broker verification report; live runs |
| 6 | Dashboard connected | Operational (research + 5 live views + comparison) | `test_dashboard_live.py`, live endpoints |
| 7 | Persistence and restart recovery | Tested (59-restart replay), Verified live (hard-kill crash test) | testing report |
| 8 | Telegram notifications | Implemented, Tested with fakes; **not operational** — no bot token/chat id configured | `test_execution_gate_and_notify.py` |
| 9 | Historical replay | Completed | testing report §1; Phase G |
| 10 | Unit tests | Completed — part of 637 passing | `python -m pytest` |
| 11 | Integration tests | Completed | testing report §3 |
| 12 | Fault injection | Completed for every implemented component; broker-side order faults only against the fake demo module | testing report §4 |
| 13 | Security tests | Completed | testing report §6 |
| 14 | Live-data shadow testing | **Started, not complete** — see §4 (hours, one trading day; the mandate requires multiple sessions and market conditions) | `shadow_and_paper_report.md` |
| 15 | Paper trading validated | **Mechanics verified, forward validation not complete** | `shadow_and_paper_report.md` |
| 16 | Demo execution validated | **Not authorized**; adapter exists, never run against the broker | `execution/mt5_demo.py` |
| 17 | Documentation updated | Completed | this directory; README; limitations register |
| 18 | No unresolved critical/high defects | **No known open defects in implemented code.** Six defects found during testing were fixed and regression-tested (implementation log, entry 3). The external blockers in §5 are not defects. | — |

## 3. Strategy version identity (deliverable C)

| Identity | Role | Evidence status |
|---|---|---|
| `xauusd_p_approved_v1` (`xauusd_p_multi_timeframe` 1.0.0) | Live runtime; Phase G | Phase G: 23 trades, −3.0R gross, all splits inconclusive (<30) |
| `xauusd_p_filter_isolated_v1` | Phase E/F baseline only | 43 trades, −5.0R gross, inconclusive |

Parameters are the approved Phase C/D values; fingerprinted in every envelope.
No parameter was changed in this programme.

## 4. Live evidence (2026-11-04)

Window 10:45–16:40 UTC on the demo feed, including the full London/New York
overlap. Details: `shadow_and_paper_report.md`.

| Measure | Shadow | Paper |
|---|---:|---:|
| Bar closes evaluated / expected | 24 / 24 | 22 / 22 |
| Median evaluation delay after bar close | 1.9 s | 2.5 s |
| Decisions reproduced from broker history (identical hash) | 24 / 24 | 22 / 22 |
| Duplicate decisions / step errors | 0 / 0 | 0 / 0 |
| Real broker disconnects → automatic recovery | 3 → 3 (37 s, 13 s, 1 s) | 2 → 2 |
| Hard-kill crash tests → clean restart | 2 → 2 | 1 → 1 |
| Candidates / positions / orders | 0 / 0 / 0 | 0 / 0 / 0 |
| Rejection mix | 100% `d1_conflict` | 100% `d1_conflict` |

No candidate occurred, so no live candidate-to-exit lifecycle has been observed.
That lifecycle is verified only on historical replay.

## 5. What remains blocked, and by whom

| Blocker | Blocks | Owner action |
|---|---|---|
| Contract economics not owner-confirmed | monetary sizing, demo | confirm `config/broker_expectations.json` (procedure in the broker report) |
| Commission schedule unknown | cost model, sizing | obtain the account's commission terms |
| News calendar not owner-confirmed | news point/pause, any production/demo entry | confirm `config/news_calendar.json` weekly |
| Broker session/holiday calendar unavailable via API | demo/live session compliance | obtain Fortune Prime's XAUUSD-P trading hours and holidays |
| Broker DST rule inferred, not confirmed | absolute historical UTC claims | confirm with the broker, or re-measure after 2027-03-14 (expected +3 h) |
| Multi-session shadow and paper forward evidence | stages 14–15 | keep `python -m trantor.runtime run` (shadow and paper) running for several weeks; review the dashboard and `tools/reproduce_live_decisions.py` |
| Demo authorization | stage 16 | explicit written authorization; then validate `execution/mt5_demo.py` with the smallest volume, write the demo report |
| Live authorization | live | separate readiness report after a successful demo stage |

## 6. Readiness statements

- **Shadow mode:** operational. Acceptance pending more sessions.
- **Paper mode:** operational. Validation pending forward evidence.
- **Demo:** not authorized. Prerequisites listed above.
- **Live:** not authorized; refused in code (`execution/gate.py`).
- **Profitability:** not established. Both historical samples are small and gross;
  the 2025 validation year was strongly negative in both variants.

## 7. Rollback and emergency shutdown

- Entries: `python -m trantor.runtime stop-entries --reason "..."` (persists; positions still managed).
- Process: Ctrl+C (graceful; never closes positions).
- Rollback: the runtime adds new files only. Deleting `state/` and `state_paper/`
  resets runtime state (positions are simulated; no broker state exists). The
  research baselines are guarded by checksum tests.
