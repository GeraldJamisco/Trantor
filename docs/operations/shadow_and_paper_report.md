# Live-Data Shadow-Mode and Paper-Trading Report

- **Date:** 2026-11-04 (Wednesday). Observation window 10:45–16:40 UTC: the
  London session, the full London/New York overlap (13:00–16:30 UTC) and the
  start of the late New York session.
- **Feed:** Fortune Prime MT5 **demo** account, symbol XAUUSD-P, read-only.
- **Strategy:** `xauusd_p_approved_v1`; news `verification_failed` (calendar
  compiled but not owner-confirmed) → labelled news-unaware; costs excluded.
- **Labels:** SHADOW = hypothetical, no orders. PAPER = in-process paper broker,
  no broker orders. Neither is live trading performance.

## 1. Acceptance criteria and results

| # | Criterion | Shadow | Paper |
|---|---|---|---|
| S1 | Demo account identity verified | ✅ `trade_mode=demo` | ✅ |
| S2 | Instrument identity verified | ✅ exact symbol, not custom, gold/USD | ✅ |
| S3 | Broker clock measured and verified | ✅ UTC+2, 3+ agreeing samples | ✅ |
| S4 | Every completed M15 bar evaluated exactly once | ✅ 24/24 bar closes (10:45–16:30) | ✅ 22/22 (11:15–16:30) |
| S5 | Evaluation timely (≤120 s after the entry bar opens) | ✅ median 1.9 s; 1 startup evaluation at 803 s (recorded, entry window would block) | ✅ median 2.5 s; max 71.5 s at startup |
| S6 | No duplicate decisions | ✅ 0 | ✅ 0 |
| S7 | Decisions reproducible from broker history | ✅ **24/24 identical record hashes** | ✅ **22/22** |
| S8 | Disconnect recovery without intervention | ✅ 3 real broker disconnects (11:08, 14:03, 16:19); recovered in ~37 s, 13 s, 1 s | ✅ 2 observed; same recovery |
| S9 | Crash recovery | ✅ 2 hard kills; integrity ok; clean reconciliation | ✅ 1 hard kill; same |
| S10 | No step errors | ✅ 0 in 18,745 steps | ✅ 0 in 17,850 steps |
| S11 | Resource use stable | ⚠️ working set 15 → 23 MB in ~5 h; plateau not yet shown | ⚠️ 16 → 30 MB |
| S12 | Multiple sessions and market conditions | ❌ one trading day, one regime | ❌ |
| S13 | At least one full candidate-to-exit lifecycle on live data | ❌ no candidate occurred | ❌ |

**Shadow acceptance: not yet complete** (S11–S13). Every mechanical criterion
that could be tested in one day passed.

## 2. What the strategy saw

All 24 decisions were rejected with **`d1_conflict`**: the D1 EMA bias was
bearish while the H4 structure was bullish, and the approved rules forbid trading
against the D1 bias. Score 0 (rejected before scoring), no candidate, no
position, no paper order. The session flag in these records shows "not
evaluated" (the rejection precedes the session step). A labelling defect that
showed it as "outside session" was found and fixed (implementation log entry 5).

Market facts observed: typical spread 0.18 (range 0.18–0.24) over 200 samples;
bid around 4,040–4,045.

## 3. Paper trading

- Mechanics verified: startup reconciliation, decision recording, gate
  evaluation, graceful shutdown (20-minute run) and hard-kill recovery.
- The full paper order lifecycle (gate → paper broker → filled order → order
  events → position → partial exits → breakeven → closure → daily risk →
  reconciliation) is **verified on historical replay** (`test_runtime_integration.py`),
  not yet on live data, because no live candidate occurred.
- Paper performance: **none to report** (0 trades). It will be reported
  separately from backtests and never combined with them.

## 4. How to continue (owner)

Run both in your own terminals so they survive this session:

```powershell
$env:PYTHONPATH='src'
python -m trantor.runtime run
python -m trantor.runtime --state-dir state_paper --settings config\settings.paper.json run
python -m trantor.dashboard
```

Review daily: the dashboard Operations page (errors, reconnects, memory),
Signals & Decisions (rejection mix), Positions & Trades. Weekly:
`python tools\reproduce_live_decisions.py --state-dir state --limit 500`.
Shadow acceptance can be declared once S11–S13 pass over several weeks covering
quiet and volatile days, an NFP or CPI day, and the daily-break and weekend
transitions.
