# Phase C Decision Report — XAUUSD-P Strategy Rules

- **Status:** Phase C documentation complete; awaiting review and explicit approval before Phase D.
- **Implementation:** None. No runtime code, tests, datasets, configuration, broker settings, or safety controls were changed.

---

## 1. Evidence Reviewed

- Current repository structure and documentation conventions. No `docs/` directory existed before this phase.
- Existing strategy protocol and domain/risk/safety boundaries.
- Phase A feasibility findings.
- Phase B acquisition manifests and validation files:
  - D1 `research_data/xauusd_p/runs/20261022T205633Z` — 725 rows, 2024-01-02 through 2026-10-21.
  - H4 `research_data/xauusd_p/runs/20261022T205633Z-01` — 4,352 rows.
  - M15 `research_data/xauusd_p/runs/20261022T205651Z` — 66,475 rows.
  - Preserved longer D1 `research_data/xauusd_p/runs/20261022T194021Z` — 4,331 rows, 2010–2026.
- Alignment report: `research_data/xauusd_p/alignment/20261022T205800Z/alignment.json`.
- D1 comparison: identical on the 725-row 2024–2026 overlap.
- Gap reports: D1 150, H4 150, M15 727; none classified as holidays.
- Cost audit: spread present; `real_volume` zero in every acquired row; tick volume present.
- Instrument metadata snapshots in each final run’s `metadata.json`.

---

## 2. Rules That Can Be Specified Now

The full deterministic specification is in:

```text
docs/strategy/xauusd_p_strategy_spec_v1.md
```

The following are now objectively specified:

- D1 EMA50/200 bias with a proposed 0.05% neutral tolerance.
- Previous Daily high/low as context only.
- H4 structural trend using confirmed 2/2 pivots.
- H4 S/R zones using ATR-based width, merging, invalidation, and expiry.
- H4 Fibonacci 0.5/0.618 zones from confirmed trend-direction swings.
- M15 RSI(14) thresholds 30/70 on the trigger candle.
- Mathematical engulfing and pin-bar definitions.
- Tick-volume confirmation using the prior 20-bar median and a 1.5× threshold.
- Eight-point scoring table and confidence tiers.
- Structural stop with ATR buffer, tick rounding, and maximum distance.
- TP1 at 2R, TP2 at 3R with a structural-obstacle adjustment.
- TP1 50% / TP2 remaining, breakeven after TP1, conservative same-bar handling.
- Mandatory H4 direction; disabled countertrend exception.
- Risk ceilings, daily loss limit, entry limit, concurrent-position limit, and cooldown.
- Missing-data and fail-safe behavior.
- Future backtest requirements and chronological split.

---

## 3. Proposed Decisions Requiring Approval

### 3.1 Daily interpretation

**Proposed:** Daily alignment gives +2; Daily neutral gives 0 and does not reject; Daily opposite
rejects; Daily unavailable/stale rejects.

**Trade-off:** This preserves the Daily score while preventing trades against an established Daily
trend. It is stricter than treating Daily as purely additive.

### 3.2 H4 trend definition

**Proposed:** H4 trend is structural, using the last two confirmed pivot highs and lows:

- bullish: higher confirmed high and higher confirmed low;
- bearish: lower confirmed high and lower confirmed low;
- otherwise neutral.

**Trade-off:** More structural than an H4 EMA pair, but slower to turn and dependent on pivot
parameters.

### 3.3 Pivot and zone parameters

**Proposed:** pivot left/right = 2/2; ATR14; S/R merge 0.5 ATR; zone half-width 0.25 ATR;
invalidation 0.25 ATR; zone/Fib expiry 120 H4 bars; Fibonacci band 0.25 ATR; minimum leg 1.0 ATR.

These are starting values, not optimized values. They need approval and later robustness testing.

### 3.4 RSI, pattern, and volume roles

**Proposed:** RSI, reversal pattern, volume, and news are score components, not individually
mandatory filters. H4 trend, H4 zone, session, news safety, data completeness, and score >=5 are
mandatory.

**Reason:** If all four components were mandatory, the minimum qualifying score would be 8, making
the 5–6 medium tier meaningless.

### 3.5 Order blocks and liquidity pools

**Proposed:** Defer them from the v1 active strategy. Candidate definitions are documented for future
review, but they will not score or trigger entries in v1.

**Reason:** They are absent from the approved eight-point table, and the current evidence does not
support a validated objective definition. Adding them would risk subjective double-counting.

### 3.6 M15 contiguity and gaps

**Proposed:** Require the trigger and prior 20 M15 bars to be present on the 15-minute grid
(21 contiguous bars). Reject if any expected slot is missing.

**Trade-off:** This will block entries immediately after session/weekend gaps and may reduce trade
frequency, but it prevents unreliable trigger/volume calculations.

### 3.7 Daily/H4 staleness

**Proposed:** Reject if the latest completed D1 bar is older than 5 calendar days, or the latest
completed H4 bar is older than 72 hours.

**Trade-off:** Holiday/weekend gaps can trigger rejection. The rule is conservative and avoids
trading on stale context.

### 3.8 Stop and target policy

**Proposed:**

- Stop beyond the lower of zone/swing support for longs (or higher of zone/swing resistance for
  shorts), plus 0.25 H4 ATR; maximum stop 3.0 H4 ATR.
- TP1 = 2R; TP2 = 3R.
- If a confirmed opposing H4 swing is before TP1, reject.
- If it lies between TP1 and TP2, cap TP2 before the obstacle; reject if TP2 < 2.5R.
- No fixed-lot sizing; partial exits require volume >= 2 × volume step.
- Breakeven after TP1 at entry until verified costs allow a cost-adjusted breakeven.

### 3.9 Daily loss boundary and cooldown

**Proposed research boundary:** 00:00 UTC. This is not verified as the broker’s trading day.

**Proposed cooldown:** 16 completed M15 bars after any loss; 32 after two consecutive losses;
instrument-specific; persisted across restarts; daily loss lockout overrides.

These durations are policy proposals, not proven optima.

### 3.10 Session interpretation

**Proposed:** Compute London/New York overlap from IANA zones:

```text
London:   08:00–16:30 Europe/London
New York: 08:00–17:00 America/New_York
```

Research may use the overlap with `session_calendar_verified=false`. Production/demo trading is
blocked until the XAUUSD-P session and holiday calendar is verified.

### 3.11 News fail-safe

**Proposed:** Production/demo: no new entries if the calendar is unavailable, stale, incomplete, or
failed. Research may use a clearly labelled `news_unaware` variant with no +1 point and no pause,
but its results cannot claim news compliance or live readiness.

### 3.12 Backtest split

**Proposed:** 2024 development, 2025 validation, 2026 through 2026-10-21 untouched holdout. No
random shuffling and no tuning on the holdout.

---

## 4. Rules Blocked by Missing Data or Unverified Economics

### Blocked now

- Monetary position sizing.
- Actual risk amount per trade.
- Broker execution or demo order submission.
- Cost-verified breakeven.
- Net-profitability claims.
- Live/demo news-filter compliance.
- Live/demo session-calendar compliance.

### Required evidence

- Contract-size convention.
- Monetary meaning of tick value.
- Verified value per price unit in account currency.
- Margin requirement and leverage treatment.
- Commission, financing/swap, and slippage.
- Stop-level, freeze-level, and execution constraints.
- Demo/live specification parity.
- XAUUSD-P session and holiday calendar.
- Verified economic-calendar source and historical coverage.
- Reliable DXY and US10Y sources, if those optional inputs are ever researched.

---

## 5. Deferred Features

- Order blocks — candidate definition only; not active.
- Liquidity pools — candidate definition only; not active.
- DXY context — optional future research; no score or filter.
- US 10-year yield context — optional future research; no verified data source.
- News-calendar integration — required before production, unavailable now.
- Monetary position sizing — blocked.
- Broker execution — out of scope.
- Telegram, scheduling, dashboards — out of scope.

---

## 6. Remaining Contradictions and Risks

### Contradictions / interpretation issues

1. The strategy calls RSI, reversal candle, volume, and news “conditions,” but the eight-point table
   makes them score components. The specification treats them as scored, not individually mandatory.
2. Daily alignment is scored +2, but the document also says to trade with higher-timeframe support.
   The proposal rejects an opposing Daily bias while allowing Daily-neutral medium setups.
3. Order blocks and liquidity pools are intended components but have no approved score slots. They
   are deferred rather than silently added.
4. The news safety filter and news score are separate. The filter is mandatory; the point is
   optional.
5. XAUUSD-P sessions remain unverified; the IANA overlap is a preferred research window, not a
   verified instrument calendar.
6. H4/M15 history covers only ~2.8 years. The 2010–2026 D1 history can provide EMA warm-up, but the
   synchronized multi-timeframe sample is limited.
7. 150 D1/H4 and 727 M15 gaps are unclassified. The contiguity rule is a conservative response.
8. `real_volume` is zero; tick volume is the only volume evidence.

### Risks

- Overfitting proposed thresholds to a short 2024–2026 sample.
- Too few trades under strict H4, zone, score, session, and contiguity filters.
- Parameter sensitivity of pivots, ATR buffers, and volume baseline.
- Inability to verify news effects historically.
- Cost uncertainty materially changing expectancy.
- Session/gap handling rejecting otherwise valid setups.
- Structural trend definitions lagging price.
- Order-block/liquidity deferral leaving the strategy less selective than originally envisioned.

---

## 7. Approval Checklist Before Phase D

The user must explicitly approve or modify:

1. Daily opposite/neutral interpretation.
2. H4 structural trend definition and 2/2 pivot confirmation.
3. S/R zone merge, width, invalidation, and expiry parameters.
4. Fibonacci swing selection, band width, and invalidation.
5. Deferral of order blocks and liquidity pools from v1.
6. RSI trigger-only window.
7. Engulfing and pin-bar mathematical definitions and thresholds.
8. Tick-volume baseline (20), median, and 1.5× threshold.
9. M15 21-bar contiguity requirement.
10. D1 5-day and H4 72-hour staleness limits.
11. Mandatory-vs-scored interpretation of RSI/pattern/volume/news.
12. Stop buffer, maximum stop distance, and structural-reference precedence.
13. TP1 2R / TP2 3R and obstacle adjustment.
14. Breakeven-at-entry until costs are verified.
15. Risk ceilings, daily loss boundary, daily entry count, concurrent limit, and cooldown durations.
16. IANA London/New York overlap and research-vs-production session behavior.
17. News fail-safe and research `news_unaware` mode.
18. Development/validation/holdout periods.
19. Whether Phase D may begin only after all approvals, or whether a smaller research subset may
    start earlier.

Until these are approved, the specification is a review draft, not an implementation contract.

---

## 8. Verification

- Runtime code, tests, dependencies, trading rules, broker settings, and safety controls were not
  modified.
- Historical datasets and manifests were not modified or regenerated.
- No strategy, backtest, sizing, news, macro, notification, scheduling, or execution code was added.
- Documentation-only files created:
  - `docs/strategy/xauusd_p_strategy_spec_v1.md`
  - `docs/strategy/phase_c_decision_report.md`
- `git diff --check` will be run after this report is written.
- No code tests are required by this documentation-only phase; the existing suite is not claimed to
  have changed.

---

## 9. Recommendation

**The specification is ready for review.** It is sufficiently deterministic for two independent
implementers to reach the same decisions once the approval checklist is resolved.

**Phase D should not begin immediately.** The remaining blockers are not coding problems; they are
policy and data-verification decisions, especially Daily/H4 interpretation, volume/RSI scoring,
session/news behavior, and monetary sizing. Resolve or explicitly defer those first.
