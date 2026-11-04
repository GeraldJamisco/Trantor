# Phase G — First Historical Evaluation of the Complete Approved Strategy

**GROSS BASELINE - EXECUTION COSTS EXCLUDED · NEWS-UNAWARE · UTC VIA INFERRED BROKER DST RULE · RESEARCH ONLY**

- **Experiment:** `phase_g_approved_utc_v1` → `research_results/xauusd_p/phase_g_approved_utc_v1/`
- **Strategy:** `xauusd_p_approved_v1` (unchanged parameters; IANA London/New York overlap session filter active)
- **Config fingerprint:** `dbcaf990c1742c57d5d81e9cb873eacfa274c80df326f27e3f5ce60b25866f32`
- **SHA-256:** summary `b7264471…`, trades `973d23d5…`, decisions.jsonl `0b2f7109…` (66,403 canonical records)
- **Runner:** `python -m trantor.research.experiments` (refuses to write into a non-empty directory)
- **Baselines:** Phase E/F artifacts untouched (`tests/test_baseline_immutability.py` passes)

## 1. Why this experiment exists

Phase E evaluated the *filter-isolated* variant, which skips the session filter.
The approved strategy's session filter is defined in UTC (via IANA zones), but
the datasets are broker server time. On 2026-11-04 the broker clock was measured
live at UTC+2, and the data shows the daily break at 00:00–01:00 server time in
every DST regime, including 43 trading days with US DST on and EU DST off. That
fits server time = New York + 7 h. Phase G converts every bar with that rule and
runs the unchanged approved strategy through the unchanged backtester.

What changed versus Phase E: strategy identity (session filter active) and the
time coordinate. Nothing else. No parameter was tuned on any split, and the
holdout was evaluated once.

## 2. Results

| Split | Trades | Wins / losses | Win rate | Gross R | Expectancy | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024 development | 8 | 3 / 5 | 37.5% | −0.50R | −0.06R | 0.90 | 2.0R |
| 2025 validation | 13 | 2 / 11 | 15.4% | −7.50R | −0.58R | 0.32 | 10.0R |
| 2026 holdout (to 10-21) | 2 | 2 / 0 | 100% | +5.00R | +2.50R | n/a | 0 |
| **Overall** | **23** | **7 / 16** | **30.4%** | **−3.00R** | **−0.13R** | **0.81** | **10.0R** |

Exits: 16 initial stops, 7 TP1 partials, 4 TP2 completions, 3 breakeven stops;
3 ambiguous stop-first bars, 2 gap exits. All 23 trades were long and medium
confidence: without a verified news calendar the maximum attainable score is 7,
and no setup reached it.

**Every split has fewer than 30 closed trades: all results are inconclusive.**
The two-trade holdout carries no evidential weight. Nothing here supports a
profitability claim.

Top rejection reasons (66,380 rejections): H4 neutral 20,469 · M15 gap 14,438 ·
no H4 zone 12,951 · D1 conflict 12,654 · **outside session 3,781** · stop
unavailable 786 · score below 5: 290 · cooldown 39.

## 3. Comparison with the Phase E baseline (descriptive)

| | Phase E (filter-isolated, dataset time) | Phase G (approved, UTC) |
|---|---:|---:|
| Trades | 43 | 23 |
| Gross R | −5.00R | −3.00R |
| PF | 0.83 | 0.81 |
| Max DD | 17R | 10R |
| 2025 validation | 21 trades, −15.5R | 13 trades, −7.5R |

Session placement of the 43 Phase E trades after converting to true UTC:

| Phase E trades | Count | Gross R | Of which 2025 |
|---|---:|---:|---|
| Inside the London/NY overlap | 16 | −3.5R | 8 trades, 0 wins, −8.0R |
| Outside the overlap | 27 | −1.5R | 13 trades, 2 wins, −7.5R |

Sixteen of Phase G's 23 trades are the same UTC entries as Phase E trades. The
other seven became possible because session-filtered trades no longer occupied
concurrency or cooldown.

## 4. Observations (not causes)

1. The session filter roughly halves trade count and drawdown, but does not
   change the sign of the result. The 2025 losing streak is present both inside
   and outside the session window: inside the overlap, 2025 had 8 trades and 0 wins.
2. The strategy was long-only in practice (D1/H4 bullish regime over 2024–2026);
   directional asymmetry cannot be assessed.
3. Score effectiveness cannot be assessed: with news unavailable every trade was
   medium confidence (score 5–6).
4. Costs are excluded. With an observed typical spread of 0.18 and typical
   structural stops of several dollars, spread costs are small per trade in R,
   but they are not modelled here. This is a gross result.

## 5. Recommended next research (owner decision required)

- Re-run Phase G after the broker confirms its server-time/DST rule. If the rule
  differs, the session classification of some bars changes.
- A versioned cost-adjusted variant once spread/commission are owner-confirmed.
- Further forward evidence from live shadow/paper operation. Historical samples
  are too small for any tuning, and the holdout must not be reused for it.
- Investigating the 2025 initial-stop cluster (entry quality vs. stop distance)
  needs a new pre-registered experiment with its own identity; none is run here.
