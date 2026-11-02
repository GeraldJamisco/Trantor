# Phase E Readiness Review

- **Date:** 2026-11-02
- **Repository:** `C:\xampp\htdocs\Trantor`
- **Instrument:** `XAUUSD-P` only
- **Planning baseline:** `docs/strategy/phase_e_backtest_plan.md`
- **Owner decisions:** Recorded in §9 for dataset time, gross R accounting, splits/state, conservative fills, and cost/interface guardrails.
- **Scope:** Readiness review plus the authorized Phase E implementation record. No full historical experiment, adjusted performance, monetary sizing, or order activity.
- **Authorization:** Phase E backtester implementation is authorized. The full historical experiment, including the holdout run, has not been executed and still requires explicit owner execution instruction.
- **Overall recommendation:** **The research-only session-isolation interface, canonical research-decision record, and deterministic Phase E research backtester are implemented and tested, with structural identity and fail-closed compliance limitations. The full historical experiment has not been run. NO-GO for live execution, absolute-UTC/session-valid interpretation, cost-adjusted performance, monetary sizing, demo trading, or profitability claims.**

---

## 1. Executive Summary

A gross price/R historical research backtest is mechanically feasible, but it cannot yet be represented as an absolute-UTC, session-verified, or cost-adjusted validation of the strategy.

The owner has now recorded decisions for the initial research backtester, including dataset time, a research-only filter variant, gross R accounting, chronological state continuity, conservative fills, and gross-only costs. Those decisions are recorded in §9. The previously identified session-filter interface prerequisite has been implemented as a separate `XAUUSDPMTFResearchVariant`; the approved strategy retains its mandatory session filter. A versioned record contract and serializer now preserve decision identity, session filter mode, plan and limitations.

The owner decisions resolve the prior continuous-state and daily-R ambiguities at the research-design level. The Phase E research backtester is implemented; the full historical experiment and holdout remain unexecuted. The main readiness conclusions are:

- **Timestamp integrity:** the stored data is internally consistent, UTC-labelled, grid-aligned and checksum-verified, but the absolute UTC provenance of the MT5 epoch remains **UNVERIFIED**. The owner decision permits a labelled **dataset-time coordinate** for initial research, provided absolute UTC and session-compliance claims are not made.
- **Strategy interface:** the detailed `StrategyDecision`/`XAUUSDPSignalCandidate` carries TP1, TP2, partials, breakeven and research-only state. The bare `TradeSignal` does not. The backtester must consume the detailed decision, not `TradeSignal`.
- **Gross accounting:** Owner Decision 2 now defines the per-exit R formula, 00:00 dataset-time exit attribution, realized-vs-unrealized separation, and the daily realized-R peak-to-trough drawdown. Owner Decision 3 defines continuous state and split-primary treatment. These are research accounting rules, not monetary returns.
- **Costs:** only the gross baseline is ready. Observed-spread adjustment remains UNVERIFIED/BLOCKED; zero spreads cannot be treated as zero cost; commission, financing, slippage, margin and account-currency costs remain unavailable.
- **Data/evaluation:** D1 overlap reconciliation passes exactly; the long D1 series can supply pre-2024 warm-up only. The 2024/2025/2026 split and 30-closed-trade preliminary threshold are defined, but remain subject to timestamp, session, sample-size and R-accounting limitations.
- **Execution:** monetary sizing, account daily-loss compliance, demo/live trading and broker order submission remain BLOCKED.

The Phase E research backtester is implemented and its focused tests pass, but the full historical experiment and holdout run were not executed. The implementation is separate from broker and execution paths.

---

## 2. Evidence Reviewed

Documents:

- `docs/strategy/phase_e_backtest_plan.md`
- `docs/strategy/research_record_contract.md`
- `docs/strategy/phase_e_prerequisite_verification.md`
- `docs/strategy/phase_e_backtest_implementation.md`
- `docs/strategy/task_1_targeted_verification.md`
- `docs/strategy/task_2_spread_verification.md`
- `docs/strategy/xauusd_p_strategy_spec_v1.md`
- `docs/strategy/phase_d_implementation_audit.md`

Implementation and interfaces:

- `src/trantor/strategy/xauusd_p.py`
- `tests/test_xauusd_p_strategy.py`
- `src/trantor/models.py`
- `src/trantor/strategy/__init__.py`
- `src/trantor/market_data/` provider and service interfaces
- `tools/mt5_acquire_xauusd_p.py`

Data metadata:

- D1 final: `research_data/xauusd_p/runs/20261022T205633Z`
- H4 final: `research_data/xauusd_p/runs/20261022T205633Z-01`
- M15 final: `research_data/xauusd_p/runs/20261022T205651Z`
- Long D1 warm-up: `research_data/xauusd_p/runs/20261022T194021Z`
- Alignment report: `research_data/xauusd_p/alignment/20261022T205800Z/alignment.json`
- D1 comparison and spread verification results from the prior reports

This readiness review is a documentation record. The separately authorized research-variant interface was implemented in the strategy and test files listed in `research_variant_session_interface_design.md`; no dataset, acquisition, broker, safety, dependency, or Phase E plan file was modified.

---

## 3. Timestamp Integrity Assessment

### 3.1 Verified facts

- Stored candle timestamps are timezone-aware ISO-8601 values with `+00:00`.
- D1 timestamps are midnight; H4 timestamps are 00/04/08/12/16/20; M15 timestamps are on quarter-hour boundaries.
- Acquisition validation reports zero non-UTC and zero non-midnight timestamps.
- D1/H4/M15 data are internally ordered and free of duplicate timestamps in the authoritative datasets.
- D1 overlap reconciliation is exact: 725/725 OHLC matches and 0 volume mismatches.
- The long D1 and authoritative D1 hashes match their metadata.

### 3.2 Unverified absolute-UTC provenance

The provider converts MT5 candle `time` values with:

```python
datetime.fromtimestamp(integer_index(value), tz=timezone.utc)
```

Evidence: `src/trantor/market_data/mt5_provider.py:268–277`.

The repository does not independently establish whether the raw MT5 epoch represents absolute UTC or a broker-server wall-clock value. The alignment report explicitly notes that the broker server timezone is not exposed and remains unverified. UTC-labelling and native grids prove internal consistency, not absolute UTC correctness.

**Status:** **UNVERIFIED.**

### 3.3 Calculations that depend on absolute UTC

- Session overlap and session-eligibility decisions.
- The 00:00 UTC daily entry-count and daily R boundaries.
- The 2024/2025/2026 split boundaries.
- Any future news-calendar timing (news is currently news-unaware).
- Interpretation of holding time and data gaps relative to real market sessions.
- Any claim that a result is valid for actual London/New York execution hours.

Candle sequence, pivot/ATR calculations, and price-based stop/target outcomes may remain invariant under a uniform timestamp shift, but split membership and session filtering would not.

### 3.4 Can a gross research backtest proceed with this isolated?

**Yes, only with an explicit limitation:**

- Treat the stored time coordinate as **dataset time**, not verified absolute UTC.
- Label all session, daily-boundary and split results as conditional on that coordinate.
- Do not claim real-session compliance or UTC-verified news/calendar behaviour.
- Preserve the D1/H4/M15 internal relative alignment, which is verified.
- Record the owner decision in §9.1 and keep absolute-UTC/session claims out of the research report.

If the owner requires absolute-UTC validity for session and split behaviour, implementation is **BLOCKED** until broker/server-time evidence is obtained.

### 3.5 Evidence needed to remove the uncertainty

- Authoritative broker/MT5 documentation or a verified sample showing the epoch source.
- A known UTC event or raw bid/ask tick with server time and UTC time.
- Confirmation of the broker server timezone and DST behaviour.
- An independent test that maps a stored bar to a known UTC interval.

---

## 4. Strategy Decision Interface Assessment

### 4.1 Complete plan location

The complete research plan is in:

```text
StrategyDecision.candidate
  -> XAUUSDPSignalCandidate.plan  (TradePlan)
```

The candidate contains entry time/reference, stop, TP1, TP2, risk distance, 50/50 partial fractions, breakeven, blocked sizing status and execution readiness. `StrategyDecision` carries the score, rejection reasons, context, and research mode.

### 4.2 `TradeSignal` conversion

`XAUUSDPSignalCandidate.to_trade_signal` at `src/trantor/strategy/xauusd_p.py:572–588` maps TP1 to the single `take_profit` field. The existing `TradeSignal` does not carry:

- TP2;
- entry timestamp;
- partial fractions;
- breakeven/cost-verification state;
- zone, score, confidence or context;
- blocked sizing/partial-exit status;
- execution readiness.

It is therefore not a complete trade-management object. It remains suitable only for nonexecutable research signalling.

### 4.3 Backtester interface recommendation

The backtester must consume:

```text
StrategyDecision.candidate
```

and, where needed, the complete `StrategyDecision` record. It must not reconstruct fills from `TradeSignal` alone.

**Status:** **READY WITH LIMITATION** — the detailed decision exposes the required TP1/TP2/partial/breakeven fields. The canonical versioned record contract in `docs/strategy/research_record_contract.md` preserves strategy/variant identity, session filter mode, timing semantics, plan and limitations; it rejects non-finite numbers, unverified session/news/cost/sizing claims, candidate-only records, and `TradeSignal` records. Identity matching is structural, not provenance. The Phase E backtester consumes the detailed decision and canonical record.

---

## 5. Gross Price/R Accounting Assessment

### 5.1 What the plan sufficiently defines

The approved plan defines:

- entry at the next M15 open;
- stop-first ambiguity rule;
- gap-through-stop at the open;
- gap-through-target at the target price, not a better gap price;
- TP1 at 50% and TP2 at remaining 50%;
- TP2 inactive until TP1 fills;
- breakeven after TP1;
- one target event per bar;
- open/truncated treatment;
- hard split cutoffs and entry-boundary exclusion;
- duplicate prevention, position count, entry count and cooldown;
- warm-up outside evaluation;
- gross price/R metrics and no monetary return claim.

### 5.2 Material accounting ambiguities

Owner Decision 2 in §9.2 now records the basic per-exit R formula. The following distinction remains:

1. **R contribution formula — resolved by owner decision.**
   Owner Decision 2 records:
   - initial risk distance as entry reference minus initial stop for long, or stop minus entry for short;
   - positive-risk validation;
   - per-exit R formulas;
   - partial combinations using actual configured fractions;
   - the 2.5R TP1/TP2 example;
   - realized versus unrealized separation.

   These are research accounting rules, not monetary returns.

2. **Daily R drawdown definition — resolved by owner decision.**
   Owner Decision 3 defines:
   - realized daily R as exit contributions on that research day;
   - daily realized-R peak-to-trough drawdown from the cumulative sequence within the day, beginning at zero;
   - open-trade unrealized R and exposure reported separately;
   - cross-day/cross-split positions identified;
   - partials contributing their configured fractions;
   - realized and unrealized R not combined.

3. **State across hard split cutoffs — resolved by owner decision.**
   Owner Decision 3 requires continuous simulated state across split boundaries, with carried open positions, cooldown state and other risk-control state. A carried position counts toward concurrent limits and may prevent new entries; it is not counted as a new entry in the next split. Its later closure belongs only to separate continuity analysis. The implementation must record each carried state; the predecessor plan's hard cutoff still governs primary split metrics.

4. **Position count after partial fills.** A partially exited position should remain one active position. This is implied but should be explicit in the acceptance tests.

5. **Entry-bar exit eligibility.** The plan says the entry bar is eligible after the open and stop-first applies. The exact treatment of entry at the open versus bar high/low path should be explicitly tested.

6. **Gap-through-target at the entry/exit boundary.** The plan says target price for gap-through-target, but the treatment when the open is already beyond a target at entry or when a bar has no clear path should be specified for the harness.

### 5.3 Assessment

**Status:** **READY WITH LIMITATION.** The R formula, daily realized-R drawdown, split-state treatment and conservative entry/gap rules are owner-defined and implemented in the Phase E backtester tests. The full historical experiment has not been run.

---

## 6. Costs and Risk Limitations

### 6.1 Costs

- The primary baseline is gross price/R with no cost deductions.
- Observed-spread adjustment is UNVERIFIED/BLOCKED per Task 2.
- Zero spread values occur (11 D1, 12 H4, 15 M15) and cannot be treated as proof of zero cost.
- No commission, financing, slippage, margin or account-currency costs are available or may be invented.
- A gross run must set an explicit `costs_applied=false`/gross status and must not import or consult spread values.

### 6.2 Structural enforcement

The Phase E backtester enforces gross-only operation by:

- keeping cost handling out of the research package;
- producing a default report with `gross_only=true` and no cost deductions;
- importing no spread, commission, financing, or execution module;
- testing that reports carry `costs_applied=false` and the gross-only label.

The full historical run remains unexecuted.

### 6.3 Risk and execution

- Non-monetary research controls (entries, concurrent positions, cooldowns, duplicates) are defined and testable.
- The 3% account-equity daily-loss limit is not evaluable without verified economics and is marked `not_evaluable_monetary`.
- Monetary sizing remains blocked.
- Demo/live trading and broker submission remain blocked.

**Status:** gross cost treatment **READY WITH LIMITATION**; spread/commission/financing **BLOCKED**; monetary/execution **BLOCKED**.

---

## 7. Data and Evaluation Integrity

### 7.1 D1 reconciliation

The prior verification established:

- long D1 4,331 rows, 2010-01-04 through 2026-10-21;
- authoritative D1 725 rows, 2024-01-02 through 2026-10-21;
- 725 overlapping records;
- 725 exact OHLC matches and 0 volume mismatches;
- 0 duplicates, 0 malformed rows, 0 unordered pairs.

The long dataset may supply pre-2024 EMA warm-up only. It must not replace the authoritative evaluation-window data. Its schema differs and its weekday gaps are unclassified.

### 7.2 Splits and sample threshold

The approved plan specifies:

- development: 2024;
- validation: 2025;
- holdout: 2026 through 2026-10-21;
- hard split cutoffs;
- entry-decision attribution;
- no later-period data for primary earlier-split metrics;
- 30 closed trades per split as a preliminary minimum reporting threshold.

Owner Decision 3 now requires continuous simulated state across boundaries. A carried position counts toward concurrent limits and may prevent new entries, but it is not counted as a new entry in the next split; its eventual closure is continuity-only and excluded from earlier-split primary metrics. Primary split metrics still truncate open positions at the cutoff.

The 30-trade threshold is not statistical proof. Results below it are inconclusive; results at or above it remain descriptive with uncertainty analysis.

### 7.3 Future-data invariance

The Phase D tests include a future-aligned H4 pivot regression added in Task 1. It verifies that a future aligned pivot does not alter an earlier decision. The Phase E backtester adds future-data invariance at the as-of view boundary and deterministic replay tests; the full historical run remains unexecuted.

### 7.4 Remaining uncertainties

- Absolute timestamp provenance remains UNVERIFIED.
- XAUUSD-P session/holiday calendar remains unverified; session filtering is research-only.
- Economic calendar remains unavailable; baseline is news-unaware.
- Sample size may be below 30 per split; that must be reported, not optimized.
- Spread-adjusted results remain blocked.
- Account-level daily loss is not evaluable.

### 7.5 Conditions before performance results are meaningful

- Dataset-time limitations are explicitly recorded and accepted for the research variant.
- Owner-approved per-exit R formula and daily realized-R drawdown are implemented as research rules.
- The approved strategy retains its mandatory session filter; the separate research-only variant is used explicitly for filter-isolated research.
- Continuous state and split-primary reporting rules are recorded and tested.
- Strategy version/parameters and split policy are frozen.
- Data checksums and manifests pass.
- Gross-only cost status is enforced.
- Holdout is run once and not used for tuning.
- Sample size and uncertainty are reported honestly.

**Status:** **READY WITH LIMITATION** for gross dataset-time research; performance interpretation remains conditional.

---

## 8. Consolidated Readiness Table

| Prerequisite | Status | Notes |
|---|---|---|
| Timestamp absolute UTC provenance | **UNVERIFIED** | Internal UTC grid and D1 overlap verified; broker epoch semantics not verified |
| Completed-bar / pivot / as-of strategy logic | **READY** | Verified in Phase D and Task 1 |
| Detailed strategy decision interface | **READY** | `StrategyDecision.candidate` exposes TP1/TP2/partial/BE |
| Phase E research backtester | **READY WITH LIMITATIONS** | Deterministic dataset-time replay, gross R fills, splits, canonical decision records, and reports implemented; full historical run not executed |
| Canonical research-decision record | **READY WITH LIMITATIONS** | Versioned serializer/validator preserves identity, filter mode, plan, timing semantics and limitations; rejects non-finite numbers and unverified compliance/cost claims; identity is structural, not provenance |
| `TradeSignal` as backtest interface | **BLOCKED / not sufficient** | TP2 and management state lost; candidate/`TradeSignal` records are rejected by the serializer |
| Gross entry/stop/target/partial/BE mechanics | **READY WITH LIMITATION** | R formula and conservative entry/gap rules implemented and tested; full historical run pending |
| Gross price/R reporting | **READY WITH LIMITATION** | Daily R and split rules owner-defined; dataset-time caveat remains |
| Owner-approved gross R formula | **READY** | Recorded in §9.2 |
| Daily R reporting and drawdown | **READY** | Realized exit R, peak-to-trough from zero, open exposure separate |
| Research-only session/news filter variant | **READY** | Separate `XAUUSDPMTFResearchVariant` implemented and tested; approved class unchanged |
| Continuous state versus split-primary metrics | **READY WITH LIMITATION** | Carried positions count for limits but later closure is continuity-only |
| D1 warm-up/evaluation reconciliation | **READY WITH LIMITATION** | Overlap exact; long gaps/schema and timestamp caveats |
| Development/validation/holdout split | **READY WITH LIMITATION** | Plan defined; timestamp/session conditional |
| 30 closed trades per split | **READY WITH LIMITATION** | Preliminary reporting threshold, not proof |
| Future-data invariance | **READY WITH LIMITATION** | Strategy test plus Phase E as-of/future-data tests; full historical run remains unexecuted |
| Gross-only cost enforcement | **READY WITH LIMITATION** | Design guardrail required |
| Observed-spread adjustment | **BLOCKED** | Unit, quote side, conversion, round-trip unverified |
| Commission/financing/slippage/margin | **BLOCKED** | No verified inputs |
| Monetary sizing | **BLOCKED** | Contract economics unverified |
| Account daily-loss compliance | **BLOCKED** | No verified account equity/sizing |
| Demo/live execution, orders | **BLOCKED** | Out of scope and prohibited |

**Overall:** **Research-only session interface, corrected research-decision record, and deterministic Phase E backtester are implemented and tested; the full historical experiment remains unexecuted.** The owner decisions resolve gross R, daily R, split-state and conservative fill rules at the research-design level. Remaining blockers include timestamp provenance, spread/cost treatment, calendar verification, sample-size interpretation, structural identity provenance and monetary/execution restrictions. **NO-GO for live execution, cost-adjusted performance, absolute-UTC/session-valid claims, monetary sizing, demo trading, or profitability claims.**

---

## 9. Owner Decisions Recorded

The following are the owner's proposed decisions for an **initial gross price/R research backtester**. They are recorded here as decisions, not as implementation authorization and not as verified production behavior.

### 9.1 Timestamp interpretation and research-only filter variant

For the initial research backtester, timestamps in the acquired datasets are treated as `dataset_time`, a research coordinate.

- Preserve the source datasets and their existing hashes.
- Do not rewrite timestamps.
- Do not claim that absolute UTC provenance has been independently verified.
- Do not make verified London/New York session-compliance claims.
- Record the dataset-time limitation in backtest reports and strategy-evaluation metadata.
- Keep the raw source timestamp and any derived timestamp transformations traceable.

The approved strategy specification remains unchanged. For the initial Phase E experiment, define a separate, explicitly labelled **research-only filter variant** because absolute UTC provenance, verified session alignment and a verified historical economic calendar are unavailable.

| Approved strategy | Initial research variant |
|---|---|
| Mandatory session filter | Session-dependent filtering isolated/disabled |
| Mandatory news safety in production; research news-unaware already permitted | News-dependent filtering isolated/disabled; no news point |
| Complete approved rule set | Same rules except the explicitly isolated filters |
| Can claim strategy behavior only within its verified constraints | Must never claim complete-strategy, session or news compliance |

Within that variant only:

- session-dependent filtering may be isolated or disabled;
- news-dependent filtering may be isolated or disabled;
- the news-confluence point must not be awarded without a verified historical calendar;
- all other approved strategy rules must remain unchanged unless another documented blocker prevents faithful implementation.

The experiment must not claim to implement or validate the complete approved strategy, and it must not claim session/news compliance. Its reports must be labelled:

```text
RESEARCH-ONLY FILTER VARIANT - NOT COMPLETE APPROVED STRATEGY
GROSS BASELINE - EXECUTION COSTS EXCLUDED
```

The required limitations and the additional evidence needed before session/news compliance can be evaluated are recorded in §11.

**Implementation status:** the session-isolation interface is implemented as the separate `XAUUSDPMTFResearchVariant` described in `research_variant_session_interface_design.md`. The approved class still has no public session-disable switch and retains the IANA overlap gate. The variant records `RESEARCH_ISOLATED`, forces news-unaware behavior, rejects verified-session claims, and has a stable variant ID/version. This does not resolve the underlying dataset-time uncertainty.

This decision does not resolve the underlying MT5 broker-timezone uncertainty.

### 9.2 Gross price/R accounting

The initial backtester uses gross simulated price movement and R-multiples only.

For a long trade:

```text
initial_risk_distance = entry_reference_price - initial_stop_price
```

For a short trade:

```text
initial_risk_distance = initial_stop_price - entry_reference_price
```

The initial risk distance must be positive. Invalid trades must be rejected or explicitly flagged; prices must not be silently repaired.

For each exit:

```text
long_exit_R  = (exit_price - entry_reference_price) / initial_risk_distance
short_exit_R = (entry_reference_price - exit_price) / initial_risk_distance
```

Partial exits are combined using their actual configured fractions of the original position. For example, closing 50% at +2R and 50% at +3R produces +2.5R gross trade R before costs.

- Track realized R separately from unrealized/open-trade R.
- Never describe gross R as account-equity return or a percentage return.
- The research-day boundary is 00:00 in `dataset_time`.
- Attribute each realized exit R contribution to the research day on which the exit event occurs.
- Report open-trade unrealized R and open exposure separately at reporting time.
- Do not claim compliance with the strategy's 3% monetary daily-loss limit. Monetary sizing and account-equity calculations remain blocked.

Daily reporting must distinguish:

1. **Realized daily R** — the sum of realized R contributions from exits whose events occur on that research day.
2. **Daily realized-R peak-to-trough drawdown** — calculated from the cumulative sequence of realized R exit contributions during that research day, beginning at zero.
3. **Open-trade unrealized R and exposure** — reported separately at the reporting time.
4. **Cross-day or cross-split positions** — identified when they affect interpretation.

Partial exits contribute their configured fraction of the original position's R outcome. A 50% exit at +2R contributes +1R; a 50% exit at +3R contributes +1.5R. Realized R and unrealized R must not be silently combined.

Trades spanning research-day boundaries must record each realized exit event on its exit day and report open exposure separately. The realized-R drawdown is a research statistic, not account-equity drawdown and not a substitute for the 3% monetary daily-loss limit.

### 9.3 Chronological splits and state continuity

Use the existing proposed chronological splits:

- Development: 2024.
- Validation: 2025.
- Holdout: 2026 through the existing holdout cutoff of 2026-10-21.
- Warm-up remains outside the evaluation windows; the D1 long history supplies pre-2024 warm-up only.

Requirements:

- Assign a trade to the split in which its entry decision occurs.
- Preserve continuous simulated state across split boundaries, including open positions, cooldown state and other relevant risk-control state.
- A carried position continues to count toward the concurrent-position limit and may prevent a new entry if that limit is reached.
- Never use a later-period exit to calculate an earlier split's primary outcome.
- Mark trades still open at an earlier split cutoff as open/truncated for that split's primary results.
- Do not count a carried position as a newly entered trade in the following split.
- Record the state carried across each boundary.
- Do not reset the simulation merely to make split results look cleaner.
- Any analysis that follows cross-boundary trades to eventual closure must be separate from primary split metrics and clearly labelled **continuity analysis**.

The backtester must distinguish **continuous replay state** from **split-specific reporting**. The carried position's later outcome is excluded from the earlier split's primary metrics and belongs to continuity analysis; its presence may still affect later-split risk state and entry eligibility as described above.

### 9.4 Entry reference and conservative fills

- Use the next M15 bar's open as the **research entry reference**, provided the decision used completed data and entry timing is valid.
- Only completed candles may contribute to a decision.
- Do not use future bars or future-confirmed information in an earlier decision.
- Do not grant favorable fills beyond the recorded entry reference because of a price gap.
- If the entry open is already beyond the proposed stop, reject the entry as invalid rather than creating a normal entry with an invalid protective structure.
- If the entry open is already beyond a proposed target, reject the entry as invalid and flag it `entry_invalid_protective_structure`; do not grant a favorable fill or assume an unrecorded path.
- If stop-loss and take-profit levels are both touched within one bar and event ordering cannot be established, use the conservative stop-first rule.
- At most one target event is credited per bar. TP2 is inactive until TP1 fills; TP1 precedes TP2; breakeven becomes active only after the required TP1 fraction fills.
- If TP1, TP2, partial exits or breakeven transitions occur on the same or adjacent bars, apply the documented sequential order and stop-first rule; do not infer tick-level path.
- A trade open at a split boundary or dataset end is marked open/truncated; later closure belongs only to separately labelled continuity analysis.
- Log ambiguous fills, gaps, truncated trades and material simulation limitations.
- Label the next-M15-open price as a **research fill reference**, not a verified executable broker fill.
- Do not invent intrabar tick data that is not present in the acquired datasets. If a transition cannot be represented unambiguously, fail closed or use a documented conservative rule and flag it.

### 9.5 Cost and strategy-interface guardrails

The initial backtest is a gross baseline. It excludes:

- spread adjustments;
- commission;
- swap or financing;
- slippage;
- margin and liquidation effects;
- other unverified execution costs.

Requirements:

- Label reports: `GROSS BASELINE - EXECUTION COSTS EXCLUDED`.
- Do not imply executable or net profitability.
- Do not invent numerical cost assumptions.
- Keep observed-spread sensitivity blocked until the spread unit, price-side convention, conversion, application rules and zero/missing-spread handling are independently verified.
- Do not change or reinterpret acquired spread values to force a cost model.
- Use `StrategyDecision` or `XAUUSDPSignalCandidate` as the strategy input. Do not use bare `TradeSignal` as the sole source of truth because it omits TP2 and partial-exit management.

Remain blocked:

- monetary position sizing;
- account-currency P&L;
- the 3% account-equity daily-loss rule;
- broker order placement;
- demo or live trading;
- net-profitability or validated-profitability claims.

The initial backtester must remain isolated from broker execution paths.

### 9.6 Approved research scope

The following is the maximum scope approved for a future initial implementation. The session-filter interface prerequisite is now implemented as a separate research variant; remaining limitations are in §13:

- XAUUSD-P only;
- gross price/R only;
- dataset-time research coordinate;
- no cost deductions;
- detailed decision/candidate input;
- TP1/TP2/partials/breakeven simulation;
- chronological development/validation/holdout reporting;
- non-monetary research controls;
- explicit line-by-line limitations and traceability.

### 9.7 Blocked capabilities

Blocked regardless of this document:

- observed-spread or any execution-cost adjustment;
- commission, financing, slippage, margin or account-currency accounting;
- monetary position sizing;
- account-equity return or 3% daily-loss claims;
- broker orders, demo trading or live trading;
- profitability or execution-readiness claims.

---

## 10. Implementation Guardrails

Any future execution or extension must:

1. Treat source data as read-only and verify hashes before use.
2. Preserve raw timestamps and record all derived time transformations.
3. Label the run as dataset-time and gross-only.
4. Consume `StrategyDecision`/`XAUUSDPSignalCandidate`; never derive fills from `TradeSignal`.
5. Keep cost modules disabled and not read spread data in the gross path.
6. Keep execution imports, broker adapters and order methods absent.
7. Record realized and unrealized R separately.
8. Record split, state origin, entry/exit period, open/truncated status and boundary flags.
9. Log all ambiguous fills, gaps, truncated trades and session/news limitations.
10. Freeze strategy version, parameters, harness version, split policy and fill convention before the holdout.
11. Mark results inconclusive below 30 closed trades per split.
12. Produce deterministic manifests and repeatable outputs.

---

## 11. Limitations to Record in Every Report

- Absolute UTC timestamp provenance remains unverified.
- The initial experiment is a research-only filter variant, not the complete approved strategy.
- Session and news compliance is not established; the initial variant must not claim it.
- The record contract rejects verified session/news claims, non-finite numbers, favorable sizing/cost claims, and unsupported fields; records remain news-unaware under schema `1.0`.
- Strategy identity matching is structural, not historical provenance; frozen dataclasses and blocked ordinary attribute assignment are accidental-modification safeguards, not tamper-proofing.
- The approved strategy has no public session-filter disable mode; the separate research variant isolates the filter explicitly.
- Observed spread and all execution costs are excluded.
- Zero or missing spread does not mean zero cost; spread adjustment is blocked.
- Monetary position sizing and account-equity loss rules are unavailable.
- Sample size may be below 30 closed trades per split.
- Trades may be open or truncated at split/data cutoffs.
- Results are gross price/R research observations, not account returns, net profitability or execution readiness.

---

## 12. Acceptance Criteria for the Initial Gross Backtester

Before results can be accepted, a future implementation must demonstrate:

### Data and time

- source checksums and row counts match the approved manifests;
- D1/H4/M15 grids and ordering pass;
- D1 warm-up/evaluation reconciliation passes;
- no unfinished candle or future bar contributes;
- dataset-time labeling and raw-to-derived timestamp traceability pass;
- split boundaries and holdout cutoff are exact.

### Strategy and interface

- every decision uses the accepted strategy with only the explicitly labelled research-only session/news filter treatment;
- the experiment is labelled as a research variant, not the complete approved strategy;
- `StrategyDecision`/candidate is the source of TP1/TP2/partial/BE fields;
- bare `TradeSignal` is not used for fills;
- no approved strategy parameters or rules changed;
- the session-filter interface prerequisite is explicitly satisfied by an authorized research interface, not a silent change;
- future-data invariance and prefix/full replay equality pass;
- rejection reasons and score breakdown are preserved.

### Fill and R accounting

- entry uses the next M15 open;
- entry-open-beyond-stop and entry-open-beyond-target cases are rejected/flagged with no favorable fill;
- stop-first, gap-stop, gap-target and one-target-per-bar rules pass;
- TP1/TP2 partial arithmetic and breakeven transitions pass;
- initial risk distance is positive and invalid trades are flagged/rejected;
- per-exit R formulas produce the documented totals, including 2.5R for 50/50 TP1/TP2;
- realized and open R are separate;
- realized daily R is attributed by exit event to the 00:00 dataset-time day;
- daily realized-R peak-to-trough drawdown starts from zero and is calculated from exit contributions within the day;
- open unrealized R and exposure are reported separately;
- partial exits use configured fractions and do not silently blend realized/unrealized R.

### Splits and controls

- trades are attributed by entry decision split;
- primary split metrics use no later-period exit for earlier trades;
- earlier-split open trades are marked open/truncated;
- carried positions count toward concurrent limits and may prevent new entries;
- carried positions are not counted as new entries in the following split;
- state carried across boundaries and continuity reporting are explicit and separate from primary metrics;
- duplicate, concurrent-position, daily-entry and cooldown controls pass;
- 30-trade threshold/inconclusive labeling is applied.

### Cost and safety

- gross-only status and `GROSS BASELINE - EXECUTION COSTS EXCLUDED` labels are present;
- spread values are not read or interpreted;
- zero/missing spread cannot produce a cost deduction;
- no commission/financing/slippage/margin or account-currency values exist;
- no execution imports or broker calls exist;
- no monetary sizing or account-loss compliance claim is made.

---

## 13. Remaining Blockers and Remaining Ambiguities

The owner decisions resolve the prior daily-R, split-state and conservative-fill design questions. The following remain:

1. **Session-filter interface — implemented.**
   The approved class remains mandatory. A separate `XAUUSDPMTFResearchVariant` isolates the session and news filters, records `SessionFilterMode.RESEARCH_ISOLATED` and a stable variant ID, and rejects verified-session claims. Ordinary instance assignment to strategy identity is blocked; identity matching is structural rather than historical provenance. The canonical research-decision record is implemented in `src/trantor/strategy/research_record.py`, documented in `docs/strategy/research_record_contract.md`, and consumed by the Phase E backtester.

2. **Research variant is not the complete approved strategy.**
   The filter-disabled experiment must be labelled as a research-only variant. It must not claim to implement or validate the complete approved strategy, session compliance, or news compliance.

3. **Observed-spread convention — blocked.**
   Raw unit, point conversion, quote side, temporal sampling and round-trip application remain UNVERIFIED. No spread-adjusted output may be produced.

4. **Sample-size interpretation — preliminary.**
   The 30-closed-trade-per-split threshold is preliminary and not statistical proof. Results below it must be inconclusive; results above it remain descriptive with uncertainty.

5. **Timestamp provenance — limited.**
   `dataset_time` is accepted as a research coordinate. Absolute UTC and real-session claims remain unresolved.

6. **Absolute monetary/account rules — blocked.**
   Monetary sizing, account-equity returns, the 3% monetary daily-loss rule, and liquidation/margin effects remain blocked.

7. **Implementation-level ambiguous-bar cases — implemented with tests.**
   The Phase E fill model tests entry-open-beyond-stop/target rejection, same-bar stop-first, TP1/TP2 sequencing, partial fractions, breakeven, gaps, and open/truncated behavior. OHLC path ambiguity remains a documented conservative convention.

8. **Cross-boundary continuity reporting — implemented with tests.**
   Carried positions count toward limits but their later closure is excluded from the entry split's primary metrics. The backtester records entry/exit periods, carried positions, and open/truncated states separately.

---

## 14. Final Recommendation

This document records owner decisions, the implemented research interface, and the authorized Phase E backtester. It **does not authorize running the full historical experiment or the holdout**.

The session-filter interface prerequisite is implemented and tested. The canonical versioned research-decision record and its integrity tests are implemented. The deterministic research backtester is implemented under `src/trantor/research/backtest/` and tested without running the full experiment. The implemented experiment remains a labelled research variant, not the complete approved strategy.

Regardless of that decision:

- **NO-GO for cost-adjusted performance** until the spread convention is verified.
- **NO-GO for absolute-UTC or session-compliance claims.**
- **NO-GO for monetary sizing, account-equity returns, or the 3% daily-loss claim.**
- **NO-GO for broker orders, demo trading, or live execution.**
- No profitability or execution-readiness claim is permitted.

---

## 15. Files Changed by the Authorized Research Implementation

```text
src/trantor/strategy/xauusd_p.py
src/trantor/strategy/xauusd_p_research.py
src/trantor/strategy/research_record.py
src/trantor/research/backtest/
tests/test_xauusd_p_strategy.py
tests/test_research_backtest.py
docs/strategy/research_variant_session_interface_design.md
docs/strategy/research_record_contract.md
docs/strategy/phase_e_backtest_implementation.md
docs/strategy/phase_e_readiness_review.md
```

No historical datasets, manifests, acquisition tools, broker settings, safety controls, dependencies, or the approved Phase E plan were modified.

---

## 16. Safety Confirmation

- The Phase E research backtester was implemented; the full historical experiment and holdout were not run.
- No full backtest execution or adjusted performance occurred during implementation.
- No monetary sizing was calculated.
- No orders were submitted, modified, cancelled, or simulated through a broker.
- No new market data was acquired or historical data regenerated.
- No broker interaction occurred.
- `git diff --check` showed no whitespace errors; only the pre-existing `README.md` LF/CRLF warning was reported.
- Repository status showed the pre-existing `README.md` modification and untracked project directories.

**Stop. Full historical execution and the holdout remain separate owner-authorized steps.**
