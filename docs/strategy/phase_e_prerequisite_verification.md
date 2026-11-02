# Phase E Prerequisite Verification

- **Verification date:** 2026-10-25
- **Repository:** `C:\xampp\htdocs\Trantor`
- **Planning baseline:** `docs/strategy/phase_e_backtest_plan.md`
- **Instrument:** `XAUUSD-P` only
- **Scope:** Read-only prerequisite audit. No backtest implementation or execution.
- **Overall status:** **NOT READY FOR PHASE E IMPLEMENTATION.** Check A has unverified data-provenance dependencies and a downstream interface limitation; Check B reconciles the D1 overlap; Check C remains unverified and blocks spread-adjusted performance.

---

## 1. Executive Summary

### Check A — Phase D source-level verification

The accepted Phase D code correctly filters to completed bars, confirms H4 pivots only after two right-side bars, uses as-of zone recomputation, passes entry time into news safety, enforces directional zone eligibility, fails closed on missing risk state, denies positive risk to rejected confidence, and fails closed on incomplete cooldown state.

Two qualifications prevent a full PASS:

- The strategy trusts the market-data provider's assumption that broker epochs are UTC. The repository explicitly states that the broker server timezone is not verified. This dependency is **UNVERIFIED**.
- `TradeSignal` carries TP1 only and cannot carry TP2, partial-exit state, blocked sizing, or execution readiness. The detailed `StrategyDecision`/`XAUUSDPSignalCandidate` does carry them. This is a **known interface limitation**, not an executable-order feature.

The existing strategy tests pass, but they do not verify acquisition provenance or a complete TradeSignal execution boundary.

### Check B — D1 dataset reconciliation

**PASS for candle reconciliation.** The two D1 files match exactly on all 725 overlapping records:

- 725 overlapping timestamps;
- 725 exact OHLC matches;
- 0 OHLC mismatches;
- 0 volume mismatches;
- 0 duplicate timestamps;
- 0 malformed rows;
- 0 unordered pairs.

The long dataset can supply pre-2024 D1 warm-up only, subject to the unverified broker timezone assumption and unclassified historical gaps. It must not replace the authoritative 2024–2026 dataset for evaluation-window records.

### Check C — Observed-spread convention

**UNVERIFIED / BLOCKED.** The acquisition code records the MT5 `MqlRates.spread` value as `spread_points`, but repository evidence does not establish:

- the definitive unit of that field;
- the quote side of the stored OHLC candles;
- whether the spread is applied at entry, exit, or as a full round trip;
- a verified point-to-price conversion for cost accounting.

The metadata reports `point=0.01`, `digits=2`, and `spread=19`, and observed spread values range from 0 to 23 points for D1/H4 and 0 to 71 for M15. Those facts support a **proposed** conversion scenario, not a verified execution-cost convention. Spread-adjusted performance remains blocked.

---

## 2. Scope and Safety Confirmation

This task was performed read-only.

No changes were made to:

- runtime code or tests;
- strategy rules;
- the approved Phase E planning document;
- historical datasets or manifests;
- broker settings or `DisabledBroker`;
- the read-only MT5 provider;
- GC/MGC analysis or the COMEX calendar;
- dependencies;
- safety controls.

No order was submitted, simulated through a broker, or executed. No backtest was implemented or run. No monetary sizing was calculated.

Only the standalone report at `docs/strategy/phase_e_prerequisite_verification.md` was created.

---

## 3. Check A: Phase D Source-Level Verification

### A1. Completed-bar timing

**Requirement:** A decision may use only completed D1/H4/M15 bars through the trigger; incomplete candles must not contribute.

**Evidence:**

- `_close_time` adds the timeframe duration to the bar open: `src/trantor/strategy/xauusd_p.py:84`.
- D1/H4/M15 are filtered by `close_time <= decision_time`: `src/trantor/strategy/xauusd_p.py:1601–1609`.
- The trigger must close exactly at `decision_time`: `1745–1746`.
- The next M15 bar must begin exactly 15 minutes after the trigger; only its open is used: `1793–1813`.
- Tests cover completed-bar filtering, incomplete candles, missing triggers, and future-bar invariance: `tests/test_xauusd_p_strategy.py:397–433`.
- The implementation validates symbol, timeframe, aware timestamps, ordering, and duplicates at `2118–2141`.

**Result:**

- **PASS** — completed-candle logic in the strategy.
- **UNVERIFIED** — data-acquisition timestamp provenance.

**Explanation and impact:**

The provider converts candle epochs using `datetime.fromtimestamp(..., tz=timezone.utc)` at `src/trantor/market_data/mt5_provider.py:275`. The provider comments state that MT5 timestamps are returned in UTC, but the repository's own alignment report notes that the broker server timezone is not exposed and remains unverified. If broker candles are not actually UTC or are subject to server-time DST treatment, D1/H4/M15 alignment, decision timestamps, session overlap, and news timing can all shift.

The strategy validates order and awareness but does not enforce native candle-boundary alignment or UTC normalization; those are delegated to the acquisition layer.

**Recommended action:** verify broker candle epoch semantics against a known UTC event or the broker server timezone, and add a Phase E harness check that rejects non-native/non-UTC grids. Do not accept acquisition provenance until this is documented and tested.

### A2. Pivot confirmation and historical information

**Requirement:** A 2-left/2-right pivot must not be exposed before both right-side bars complete; historical zones, Fibonacci levels, ATR and trend must use only information available at the decision.

**Evidence:**

- `_pivots` loops only through `len(observations) - right`, and records `confirmed_at` at the close of `index + right`: `src/trantor/strategy/xauusd_p.py:758–795`.
- `_h4_trend` uses those confirmed pivots and the as-of ATR: `798–835`.
- `_build_sr_zones` uses the as-of ATR, confirmed pivots, confirmed/index age, and invalidation over completed observations only: `838–920`.
- `_build_fib_zone` uses the latest confirmed high/low in the required order: `923–982`.
- The future-bar test is at `tests/test_xauusd_p_strategy.py:404–433`.
- The Phase D audit records as-of recomputation and future-data invariance requirements; the Phase E plan approves as-of recomputation.

**Result:** **PASS for the implementation logic**, with a **test-strength limitation**.

**Explanation and impact:**

The source logic is point-in-time for a given prefix. `_h4_trend` and zone construction use only the supplied `h4_completed` observations, and pivots are only constructed when their right-side bars exist. The accepted behavior recomputes zones with the ATR at each decision; this matches the approved Phase E as-of rule and does not retroactively alter an already stored decision.

The existing future-bar test appends an H4 bar at `12:15`, which is not a native H4 grid point. It proves general filtering but does not prove that a future **aligned** H4 pivot or future ATR cannot change an earlier decision. A stronger test should append a valid future H4 bar that would create a pivot if incorrectly included and compare the complete decision record.

### A3. Entry-time news safety

**Requirement:** News safety must use the proposed entry timestamp; the mechanism must not be confused with a verified calendar.

**Evidence:**

- `_news_evaluation` accepts `entry_time` at `src/trantor/strategy/xauusd_p.py:1180–1190`.
- The safety reference is `entry_time` when supplied and the window is checked at `1221–1229`.
- The main evaluate path passes the next-M15 open timestamp at `1867–1871`.
- `test_news_safety_window_and_score_window` checks the normal path, exactly +30 minutes, +31 minutes, +45 minutes, and a direct later-entry reference at `tests/test_xauusd_p_strategy.py:932–968`.
- `test_stale_news_calendar_is_unavailable_and_not_awarded` and `test_news_snapshot_from_the_future_is_not_used` cover unavailable/future snapshots at `970–1010`.
- Under approved contiguous timing, `entry_time = open(M15_T+1) = close(M15_T) = decision_time`; the public path therefore has no artificial 15-minute offset. The direct helper test proves the mechanism honors a later entry reference if one is ever supplied.

**Result:**

- **PASS — timestamp-handling mechanism.**
- **BLOCKED/UNVERIFIED — verified economic-calendar compliance.**

**Explanation and impact:**

No economic-calendar integration or verified historical calendar exists. The research baseline uses `news_unaware=True`, no news point and no verified 30-minute pause. The code correctly fails closed in production mode when the calendar is unavailable, stale, incomplete, or future-dated. It must not be described as news-compliant.

**Recommended action:** keep news-unaware labelling and do not claim compliance. A verified calendar is a separate data-integration prerequisite.

### A4. Directional eligibility and risk safety

**Requirement:** Longs qualify at support or bullish Fibonacci; shorts at resistance or bearish Fibonacci. Missing risk state, rejected confidence, and incomplete cooldown state must fail closed. Daily loss/trade-count controls must not be assumed monetarily evaluable.

**Evidence:**

- Directional helpers and selection: `src/trantor/strategy/xauusd_p.py:1025–1065`.
- Directional tests: `tests/test_xauusd_p_strategy.py:635–689`.
- Missing risk state returns `RISK_UNAVAILABLE`: `src/trantor/strategy/xauusd_p.py:1536–1578`; test at `1285–1312`.
- Rejected confidence returns zero risk and ineligible: `1284–1291`; test at `1314–1324`.
- Cooldown missing timestamps fails closed: `1303–1327`; tests at `1220–1283`.
- Daily loss, entry count, and concurrent-position checks: `1292–1338`.
- Tests for risk controls and multiple reasons: `1220–1283`, `1331–1338`.

**Result:**

- **PASS** — directional S/R and Fibonacci eligibility.
- **PASS** — missing risk state, rejected confidence, cooldown, entry-count and concurrent-position fail-closed behavior.
- **BLOCKED/UNVERIFIED** — account-level 3% daily-loss compliance.

**Explanation and impact:**

The daily-loss calculation uses caller-supplied `equity` and `start_of_day_equity`; no verified account-equity feed, monetary sizing, or contract economics exists. The code can reject a supplied loss state, but Phase E cannot claim that the account-level 3% limit was enforced. The Phase E plan correctly requires daily R drawdown and `daily_loss_limit_status=not_evaluable_monetary`.

### A5. Signal and execution boundary

**Requirement:** The strategy must remain research-only, and downstream interface limitations must be explicit.

**Evidence:**

- The strategy module has no execution/broker imports or order methods.
- `TradeSignal` is documented as nonexecutable and contains no quantity/order fields: `src/trantor/models.py:181–199`.
- `XAUUSDPSignalCandidate.to_trade_signal` maps TP1 to the single `take_profit` field: `src/trantor/strategy/xauusd_p.py:572–588`.
- TP2 remains in `XAUUSDPSignalCandidate.plan.tp2`; candidate readiness and blocked sizing are at `546–570`.
- Regression test: `tests/test_xauusd_p_strategy.py:1192–1207`.
- Monetary sizing status is explicitly blocked; partial-exit status is explicitly blocked.

**Result:**

- **PASS** — research-only implementation; no execution code.
- **FAIL — interface completeness for downstream partial-exit handling.** `TradeSignal` cannot carry TP2, partial-exit state, blocked sizing, or execution readiness.
- **BLOCKED/UNVERIFIED** — contract economics, volume feasibility, commission, financing, slippage, margin.

**Explanation and impact:**

The detailed `StrategyDecision`/`XAUUSDPSignalCandidate` contains TP2 and blocked status, but a consumer that reads only `TradeSignal` cannot see the complete plan. The existing model's documentation mitigates the execution risk, but it does not make the interface complete.

**Recommended action:** Phase E must consume the detailed decision for fills and research state. A future enriched signal model requires separate design approval; no execution interpretation is permitted.

### A6. Executed source-level tests

Actual read-only results:

```text
$env:PYTHONPATH='src'; python -m pytest tests/test_xauusd_p_strategy.py -q
57 passed
```

```text
$env:PYTHONPATH='src'; python -m pytest -q
406 passed
```

These tests are deterministic and do not use live MT5 data. They establish the covered synthetic behaviors, but they do not verify acquisition provenance, a real economic calendar, contract economics, or a verified spread convention.

---

## 4. Check B: D1 Dataset Reconciliation

### 4.1 Files and hashes

| Dataset | File | SHA-256 |
|---|---|---:|
| Long D1 2010–2026 | `research_data/xauusd_p/runs/20261022T194021Z/candles.csv` | `3847390c3cdde0c98ea22bcab4d099cbab4ef17a8d73eb27c2c502819b0299d6` |
| Authoritative D1 2024–2026 | `research_data/xauusd_p/runs/20261022T205633Z/candles.csv` | `353a2452d01248e12973d6c17f93de6c3f7979aa089b7783d36d048182864947` |

Both hashes match their `metadata.json` checksum fields. No file was modified.

### 4.2 Row counts and ranges

| Dataset | Rows | First UTC | Last UTC | Metadata schema |
|---|---:|---|---|---:|
| Long D1 | 4,331 | 2010-01-04T00:00:00+00:00 | 2026-10-21T00:00:00+00:00 | 1 |
| Authoritative D1 | 725 | 2024-01-02T00:00:00+00:00 | 2026-10-21T00:00:00+00:00 | 2 |

Both use the header:

```text
time_utc,open,high,low,close,volume
```

Timestamps are ISO-8601 UTC (`+00:00`), all are midnight D1 boundaries, and validation reports zero non-UTC and zero non-midnight timestamps.

### 4.3 Overlap comparison

- Overlap range: `2024-01-02T00:00:00+00:00` through `2026-10-21T00:00:00+00:00`.
- Overlapping records: **725**.
- Exact OHLC matches: **725**.
- OHLC mismatches: **0**.
- Volume mismatches: **0**.
- Long-only records: **3,606**, all before 2024-01-01.
- Authoritative-only records: **0**.
- Missing overlap records: **0** — every authoritative timestamp is present in the long dataset.
- Missing dates in the broader history: 52 weekday gaps in the long validation report and 150 reported gaps in the authoritative report, unclassified because the session/holiday calendar is unverified.

### 4.4 Data integrity checks

- Duplicate timestamps: **0** in both files.
- Malformed rows: **0** in both files.
- Unordered timestamp pairs: **0** in both files.
- Invalid OHLC rows: validation reports **0** for both.
- Long validation: accepted; 4,331 rows; 0 duplicates; 0 non-UTC; 0 non-midnight; 52 weekday gaps, unclassified because the XAUUSD-P session/holiday calendar is unverified.
- Authoritative validation: accepted; 725 rows; 0 duplicates; 0 non-UTC; 0 non-midnight; 150 reported gaps, including 7 weekday gaps; gaps not classified as holidays.

The long dataset has schema 1 and lacks the authoritative dataset's cost/instrument metadata fields. That is a metadata-format difference, not an OHLC discrepancy.

### 4.5 Result

**PASS for candle reconciliation.** The 725-record overlap is exact on OHLC and volume. There is no unresolved candle-data contradiction.

**Warm-up use:** **PASS WITH LIMITATION.** The long dataset can supply pre-2024 D1 closes for EMA warm-up only, because:

- it contains 3,606 pre-2024 records;
- all overlapping records match exactly;
- it is ordered and free of duplicates/malformed rows.

It must not replace the authoritative dataset for 2024–2026 decision records. Its 52 unclassified weekday gaps and older schema are limitations, not reasons to merge or alter it, and no decisions will be taken pre-2024. The D1 reconciliation requirement in the Phase E plan remains satisfied only after the warm-up rows and authoritative rows are kept as separate source spans with both hashes recorded.

**Residual limitation:** the broker timezone/epoch assumption still applies to both datasets; exact overlap does not verify absolute UTC semantics.

---

## 5. Check C: Observed-Spread Convention

### 5.1 What is stored

`spread_volume.csv` has columns:

```text
time_utc, spread_points, real_volume, tick_volume
```

The acquisition code takes MT5 `MqlRates` field `spread`, converts it to `int`, and names it `spread_points`:

- `tools/mt5_acquire_xauusd_p.py:408–415`.
- It writes the value at `880–895` using `int(row["spread"])`.
- Tests preserve the value but do not establish its unit: `tests/test_mt5_acquire.py:181–195`, `435–472`.

The metadata snapshot reports:

- `point = 0.01`
- `trade_tick_size = 0.01`
- `digits = 2`
- `spread = 19`
- `trade_contract_size = 100.0`
- `trade_tick_value = 1.0`
- `real_volume = 0.0` in all observed rows.

### 5.2 Observed spread values

| Dataset | Cost rows | Min | Max | First | Real volume nonzero |
|---|---:|---:|---:|---:|---:|
| D1 | 725 | 0 | 23 | 23 | 0 |
| H4 | 4,352 | 0 | 23 | 23 | 0 |
| M15 | 66,475 | 0 | 71 | 23 | 0 |

### 5.3 Findings

1. **What the stored value represents: UNVERIFIED.** The code calls it `spread_points`, but the repository does not document or test that MT5's `MqlRates.spread` is in points rather than another unit.
2. **Point-to-price conversion: UNVERIFIED.** `point=0.01` would imply `spread_price = spread_points * 0.01` if the field is confirmed to be points, but the point value is an unverified broker metadata snapshot, not a verified contract fact.
3. **Bid/ask side: UNVERIFIED.** No stored OHLC quote convention or per-bar bid/ask side is documented. MT5 rates do not provide historical bid/ask side in the stored files.
4. **Round-trip application: UNVERIFIED / BLOCKED.** There is no evidence to determine whether to charge the spread at entry, exit, or both, or how to apply it to gaps and same-bar events. Treating the value as a full round-trip cost would be an assumption.
5. **Zero spread values:** The files contain zero spread points. They cannot automatically be interpreted as zero transaction cost or as missing data; the convention must explicitly handle them.
6. **No commission/financing/slippage/margin evidence:** none may be invented.

**Result:** **UNVERIFIED.** Spread-adjusted performance is **BLOCKED**. The primary Phase E baseline must remain gross price/R with no cost deduction.

### 5.4 Proposed testable convention, not verified

Subject to owner authorization and verification, a future testable convention could be:

1. Obtain evidence that `MqlRates.spread` is expressed in points and that the metadata point value is authoritative.
2. Determine the candle quote side from a verified source.
3. Define entry/exit side adjustments and a full round-trip application rule.
4. Add tests for long and short entries/exits, gaps, zero spreads, missing spreads and same-bar events.
5. Label the output as a scenario until all evidence is verified.

This is a proposal only. It must not be used for a performance calculation in this task.

---

## 6. Consolidated Status Table

| Check | Requirement | Result | Notes |
|---|---|---|---|
| A1 | Completed-bar timing | **PASS** | Filters to close ≤ decision; next open checked |
| A1 | Timestamp/acquisition provenance | **UNVERIFIED** | Provider assumes broker epochs are UTC; server timezone unverified |
| A2 | Pivot confirmation | **PASS** | 2-right confirmation; tests |
| A2 | As-of zones/Fib/ATR | **PASS** | Approved as-of recomputation; no future data in prefix |
| A2 | Future-pivot test strength | **UNVERIFIED** | Existing future H4 test uses a non-native timestamp |
| A3 | Entry-time news mechanism | **PASS** | Entry timestamp explicit; boundary tests |
| A3 | Verified calendar compliance | **BLOCKED/UNVERIFIED** | No calendar integration; research-unaware only |
| A4 | Directional zone eligibility | **PASS** | Support/bullish Fib for long; resistance/bearish Fib for short |
| A4 | Missing risk state / rejected confidence / cooldown | **PASS** | Fail-closed tests |
| A4 | Account-level daily loss | **BLOCKED/UNVERIFIED** | No account economics/sizing; R drawdown only |
| A5 | Research-only boundary | **PASS** | No execution/order code |
| A5 | `TradeSignal` completeness | **FAIL / limitation** | TP1 only; no TP2, partial state, or readiness |
| B | D1 overlap reconciliation | **PASS** | 725/725 exact; no duplicates/malformed |
| B | Long warm-up suitability | **PASS WITH LIMITATION** | Pre-2024 only; gaps/schema/timezone caveats |
| C | Observed-spread convention | **UNVERIFIED / BLOCKED** | Unit, side, conversion, and round-trip unverified |

**Overall prerequisite status: NOT FULLY VERIFIED.** Check B passes for candle reconciliation. Check A passes at the strategy-code level but has unverified acquisition provenance and a downstream interface limitation. Check C remains UNVERIFIED/BLOCKED.

---

## 7. Remaining Blockers

1. Verify broker candle epoch/UTC semantics and server-time handling before relying on D1/H4/M15 alignment.
2. Document and test the observed-spread unit, point-to-price conversion, quote side, zero/missing handling, and round-trip application.
3. Keep spread-adjusted performance blocked until the convention is verified.
4. Resolve the `TradeSignal` partial-exit representation: Phase E must consume the detailed decision, or a separate interface change must be approved.
5. Verify contract economics, volume step, commission, financing, slippage and margin before any monetary sizing.
6. Verify the XAUUSD-P session/holiday calendar and economic calendar; research mode remains non-compliant at production level.
7. Add the stronger future-aligned H4 pivot invariance test before Phase E implementation.
8. Reconcile the long D1 warm-up metadata/gap classification with the authoritative dataset and retain both source spans without merging files.

---

## 8. Recommended Next Action

Do not authorize Phase E implementation yet.

Recommended order:

1. Verify and document broker timestamp/UTC semantics.
2. Complete a verified spread-convention test and keep spread-adjusted results blocked until it passes.
3. Define the downstream `TradeSignal` versus detailed-decision contract.
4. Add the future-aligned pivot test and a full source-level acceptance record.
5. Re-run this prerequisite verification after those items are resolved.

No strategy rule changes, backtest implementation, sizing, or execution work should begin during that process.

---

## 9. Final Confirmation

- Only the new report `docs/strategy/phase_e_prerequisite_verification.md` was created.
- No runtime code, tests, strategy rules, historical datasets, manifests, dependencies, broker settings, or safety controls were changed.
- No order was submitted, no broker simulation was performed, and no backtest was run.
- No monetary sizing was calculated.
- The Phase E planning document was not modified.
- The final repository status/diff inspection is recorded separately in the audit output; `git diff --check` showed no whitespace errors and only the pre-existing `README.md` LF/CRLF warning.

**Stop and await explicit authorization for the next stage.**
