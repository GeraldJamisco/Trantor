# Phase E Backtest Planning

- **Status:** Planning only. No backtest engine, sizing, execution, or trading code is authorized.
- **Phase D status:** Accepted provisionally as the research baseline, subject to source-level verification.
- **Instrument:** `XAUUSD-P` only.
- **Scope:** Historical evaluation of the accepted Phase D research strategy.
- **Implementation authority:** None. This document defines the architecture, methodology, prerequisites, tests, and acceptance criteria for a separately authorized Phase E implementation.

---

## 1. Purpose and Non-Negotiable Boundaries

Phase E is intended to determine whether the accepted Phase D rules can be evaluated reproducibly on the saved XAUUSD-P data. It is not intended to establish profitability, statistical validity, or execution readiness.

### 1.1 Instrument scope

Phase E is limited to **XAUUSD-P**.

**GBPJPY-P is a separate future research workstream.** It must not be added to the Phase E gold backtest, and the existing gold strategy must not be changed to accommodate GBPJPY-P.

### 1.2 Non-negotiable boundaries

The following remain prohibited:

- monetary position sizing;
- broker order submission;
- demo or live trading;
- treating `TradeSignal` as an executable instruction;
- automated scheduling of execution;
- modifying `DisabledBroker` or the read-only MT5 provider;
- changing GC/MGC analysis or the COMEX calendar;
- modifying historical datasets or manifests;
- adding dependencies without separate approval;
- changing the approved Phase D strategy rules;
- implementing or executing a backtest, including the holdout run, without separate explicit authorization.

The backtest must use `XAUUSDPMultiTimeframeStrategy`; it must not reimplement the strategy in a parallel engine.

---

## 2. Accepted Phase D Baseline

The accepted strategy implementation is:

```text
src/trantor/strategy/xauusd_p.py
```

Accepted behavior includes:

- completed D1/H4/M15 bars only;
- decision at the M15 trigger close;
- entry reference at the next M15 open;
- D1 EMA 50/200 bias;
- structural H4 2/2 pivots;
- directional H4 S/R and Fibonacci zones;
- merged S/R center ± ATR half-width;
- M15 RSI, engulfing/pin, tick-volume and 21-bar contiguity rules;
- eight-point scoring and mandatory filters;
- structural stops, 2R TP1, 3R TP2, obstacle cap and 2.5R minimum;
- 50/50 partials and breakeven after TP1;
- research-only risk-state checks;
- explicit research news-unaware and unverified-session states.

Phase E must not alter any of these rules to improve historical results.

---

## 3. Data Sources and Immutable Inputs

### 3.1 Primary synchronized datasets

Use the final Phase B datasets from the manifests:

| Timeframe | Run directory | Rows | First UTC | Last UTC |
|---|---|---:|---|---|
| D1 | `research_data/xauusd_p/runs/20261022T205633Z` | 725 | 2024-01-02 | 2026-10-21 |
| H4 | `research_data/xauusd_p/runs/20261022T205633Z-01` | 4,352 | 2024-01-02 | 2026-10-22 16:00 |
| M15 | `research_data/xauusd_p/runs/20261022T205651Z` | 66,475 | 2024-01-02 01:00 | 2026-10-22 20:30 |

Validation checksums:

```text
D1  353a2452d01248e12973d6c17f93de6c3f7979aa089b7783d36d048182864947
H4  70558ae65a4ce9869129f44e32c0fac2378f5b07bedd8dac812a337fd3a84fa4
M15 4b10429e2ad681bed78c65f66a1858604bea2388af8354276200515768d31535
```

Alignment report:

```text
research_data/xauusd_p/alignment/20261022T205800Z/alignment.json
```

The report confirms synchronized overlap counts of 724 D1, 4,346 H4 and 66,396 M15 bars, with `supports_synchronized_research=true`.

### 3.2 D1 warm-up history

The final D1 dataset starts in 2024 and cannot by itself supply the 201 completed D1 bars required by the EMA200 rule at the start of 2024. Use the preserved D1 history:

```text
research_data/xauusd_p/runs/20261022T194021Z
4,331 rows, 2010–2026
SHA-256: 3847390c3cdde0c98ea22bcab4d099cbab4ef17a8d73eb27c2c502819b0299d6
```

Rules:

- Pre-2024 D1 rows are **warm-up only**; no decisions are taken before the development window.
- On the 2024–2026 overlap, the preserved and final D1 datasets must be demonstrated identical or reconciled explicitly before use.
- Any discrepancy must be investigated and documented. Do not silently combine conflicting data.
- Prefer the final D1 run for decision records and use the preserved history only for prior EMA state.
- Record both source paths and checksums in the run manifest.

### 3.3 Cost files

`spread_volume.csv` is the only observed cost input. It contains:

```text
time_utc, spread_points, real_volume, tick_volume
```

`real_volume` is zero throughout the acquired data. Tick volume is the only volume evidence.

The `instrument_metadata.json` values describe the broker snapshot but do not constitute verified contract economics. They must not be used to infer account-currency risk, margin, commission, financing, or position size.

---

## 4. Point-in-Time Data and Zone Evaluation

### 4.1 Decision view

For each candidate decision timestamp `T`:

```text
decision_time = close(M15_T)
entry_time    = open(M15_T+1)
```

The historical view must contain only:

- completed D1 bars with close ≤ `decision_time`;
- completed H4 bars with close ≤ `decision_time`;
- completed M15 bars through `T`;
- a news snapshot with `as_of ≤ decision_time` (or the explicit research `news_unaware` state);
- no unfinished bar and no bar whose close is after `decision_time`.

The engine must pass a prefix window ending at the decision timestamp to the strategy, even though the strategy itself also filters. This is a defense-in-depth measure.

### 4.2 Zone evaluation rule

Phase E will use the **accepted Phase D as-of recomputation** rule:

- D1 EMA uses completed D1 closes.
- H4 ATR14 is recalculated from the H4 prefix ending at the decision.
- Pivots are included only after both right-side confirmation bars have closed.
- S/R zones are built from confirmed pivots, merged by the approved center-distance rule, and bounded by center ± half-width using the as-of ATR.
- Zone invalidation and age are evaluated using only completed H4 bars through the decision.
- Fibonacci legs use only confirmed highs/lows available at the decision.
- Zone clusters and directional eligibility are evaluated at the decision.
- No future pivot, future ATR, future close, or future data revision may alter the decision record.

This preserves the Phase D implementation as accepted. It does **not** create a persistent zone identity registry. The decision record captures the zone cluster, center, boundaries, ATR, evidence and source context so the result is auditable after the fact.

### 4.3 Reproducibility requirement

For every decision, persist an append-only record containing:

- strategy name/version;
- source dataset paths and checksums;
- decision timestamp;
- complete strategy parameters;
- D1/H4/M15 bar identities or prefix checksum;
- news/session mode;
- zone snapshot;
- score breakdown;
- rejection reasons;
- candidate/entry/stop/TP1/TP2;
- risk state used.

Re-running the same decision with additional future data must produce an identical record. Prior decision records must never be rewritten after later data is loaded.

### 4.4 Approved zone-history handling

The owner has approved **as-of zone recomputation for each historical decision**. The decision timestamp is the sole as-of boundary:

- only confirmed pivots, completed bars and ATR values available at that decision may be used;
- prior decision records and their zone snapshots are preserved without retroactive modification;
- immutable zone identities and a persistent zone registry are not introduced in Phase E;
- a historical decision must not be re-derived from future pivots, future ATR values or other later data.

This decision is resolved. Any future proposal to freeze zones at first confirmation would be a separate strategy-model change requiring Phase D review and new tests.

---

## 5. Chronological Evaluation Design

### 5.1 Split boundaries

Decisions are assigned by decision timestamp:

| Split | Decision window |
|---|---|
| Development | 2024-01-01T00:00Z through 2024-12-31T23:59:59Z |
| Validation | 2025-01-01T00:00Z through 2025-12-31T23:59:59Z |
| Holdout | 2026-01-01T00:00Z through 2026-10-21T23:59:59Z |

Rules:

- Warm-up occurs before the development window and is not counted as a split result.
- Development, validation and holdout are chronological; no shuffling, stratification by outcome, or random resampling of the timeline.
- The holdout is not inspected for parameter selection or rule changes.
- A trade is attributed to the split containing its **entry decision timestamp**, not its exit timestamp.
- Primary split evaluation uses hard split cutoffs. A position still open at the split cutoff is marked `open_or_truncated_at_split_cutoff`; it is not closed with later-split data for primary split metrics.
- A decision whose next-bar entry time falls after the split cutoff is recorded as `entry_boundary_excluded` and does not enter that split's primary trade sample.
- No split may reset EMA/ATR/pivot history. Warm-up state carries forward.

### 5.2 Minimum eligible decision point

The first evaluation decision must have:

- at least 201 completed D1 bars after D1 warm-up;
- valid H4 ATR14 and at least two confirmed pivot highs and lows;
- at least 21 contiguous M15 bars;
- the next M15 bar available;
- a valid research session/news mode.

Early 2024 decisions that fail these conditions must be recorded as rejections, not silently skipped from the rejected-setup denominator.

### 5.3 Holdout discipline

Before the first holdout run:

- freeze strategy version, parameters, harness version, fill model, cost scenarios, and report schema;
- hash the frozen configuration;
- record the trial count for development and validation;
- do not alter the plan after seeing holdout results;
- report holdout results once, with all limitations.

### 5.4 Split-boundary trade record

Every candidate and trade record must include:

- `entry_decision_period`: development, validation, holdout, or outside;
- `entry_time` and `entry_time_period`;
- `exit_time` and `exit_period`, or `not_closed`;
- `later_period_data_required`: true only when the position remains open at a split cutoff and would require later-period data to close;
- `open_or_truncated_at_split_cutoff`: true/false;
- `entry_boundary_excluded`: true when the next-bar entry falls outside the decision's split.

The holdout reporting cutoff is `2026-10-21T23:59:59Z`. Any holdout position still open at that cutoff is truncated and reported separately. No holdout outcome is used to tune development or validation rules.

A cross-boundary continuity analysis that closes earlier-split positions with later-split data is not part of the primary evaluation. It would require separate authorization and must not alter primary split metrics or feed parameter selection.

---

## 6. Backtest Architecture

No code is created by this plan. The future implementation should be a separate research package, for example:

```text
src/trantor/research/backtest/
```

Proposed components:

### 6.1 Historical view

A read-only view exposing as-of prefixes for D1, H4 and M15, with explicit close-time filtering and no mutation of source files.

### 6.2 Decision replay

A chronological loop over completed M15 decision timestamps:

1. Build the as-of view.
2. Invoke `XAUUSDPMultiTimeframeStrategy.evaluate`.
3. Record the complete `StrategyDecision`.
4. If a candidate exists, pass it to the fill model.
5. Update research position/risk state.
6. Continue to the next decision.

The replay must not skip rejected decisions from diagnostics or reports.

### 6.3 Fill model

A pure research fill simulator, separate from any broker interface:

- entry at the next M15 open;
- no fill at trigger close;
- stop-first on ambiguous bars;
- gap-through-stop at the open when worse than the stop;
- gap-through-target at the target price, not a better gap price;
- TP1 then TP2 with partial 50% units;
- TP2 inactive until TP1 fills;
- breakeven after TP1;
- no synthetic bars, no filled gaps, no impossible fills.

### 6.4 Cost model

A separate price-based cost layer. It must not calculate account-currency P&L.

### 6.5 State and records

In-memory research state only:

- open position count;
- entries today;
- consecutive losses;
- last exit/cooldown;
- processed trigger timestamps;
- realized R and open R.

No persistent storage is added in Phase E planning. A future persistence layer requires separate review.

### 6.6 Reporting

Deterministic CSV/JSON/Markdown outputs under a new research-results directory, with a run manifest and checksums. Source research data remains read-only.

### 6.7 Safety boundary

The backtest package must not import:

```text
trantor.execution
trantor.safety
```

It may consume `TradeSignal`/`StrategyDecision` only as research records. It must not call any broker or order method.

---

## 7. Fill and Position-State Assumptions

### 7.1 Entry

- Entry price = `open(M15_T+1)`.
- Entry timestamp = `open_time(M15_T+1)`.
- No trigger-close fill.
- Missing/invalid next bar = no trade.
- Late-entry and session/news filters remain active.

### 7.2 Stop and targets

For a long:

- stop price is fixed at signal construction;
- TP1 = 2R; TP2 = 3R before obstacle cap;
- obstacle rules remain exactly Phase D;
- partial exit at TP1 = 50% of normalized position;
- remaining 50% at TP2;
- after cumulative TP1 fill reaches 50%, stop becomes entry;
- if TP1 fills and the remaining position stops at breakeven, the trade is flat, not a new trade.

For a short, mirror the rules.

### 7.3 Ambiguous and gap bars

Required order:

1. If the bar opens beyond the stop, fill at the open.
2. Otherwise, if the bar touches the stop and any target, assume the stop fills first.
3. If the stop is not touched and TP1 is eligible, fill TP1 at its target price.
4. If TP1 is not already filled, TP2 is inactive.
5. If TP1 was already filled, TP2 may fill at its target price.
6. If the open gaps through TP1/TP2, use the target price, not a better gap price.
7. Do not credit a target that was already beyond the entry price when the position was opened.

To avoid path uncertainty, at most one target event may be credited on a single bar. If both TP1 and TP2 are touched on that bar, credit TP1 only; TP2 can fill on a later bar. This is a conservative, deterministic convention and must be reported as such.

### 7.4 Entry-bar management

The entry bar is eligible for stop/target simulation after the entry open. Because the intrabar path is unknown, stop-first applies. The fill engine must document this convention.

### 7.5 Missing exit data

If a position cannot be closed before the data cutoff:

- mark it open/truncated;
- report it separately;
- do not invent a close price;
- do not include it in closed-trade metrics.

### 7.6 Normalized units

Because monetary sizing is blocked:

- each trade is normalized to one research unit;
- partials are 0.5/0.5 of that unit;
- results are R-multiples and price points, not account currency;
- no compounding, lot sizing, margin, or notional is permitted;
- partial-exit feasibility remains `blocked_pending_volume_sizing`.

---

## 8. Transaction-Cost Treatment

### 8.1 Verified and unverified inputs

| Input | Status | Phase E treatment |
|---|---|---|
| Spread | Observed in `spread_volume.csv` | Price-based scenario only; not verified contract economics |
| Commission | Unverified | No base cost; scenario only if owner supplies |
| Financing/swap | Unverified | No base cost; scenario only if owner supplies |
| Slippage | Unverified | No base cost; scenario only if owner supplies |
| Margin/leverage | Unverified | Not used |
| Real volume | Zero throughout | Not used |
| Tick volume | Available | Strategy input only |

The presence of a spread file does not verify account-currency costs, execution quality, or contract economics.

### 8.2 Primary baseline

The primary baseline is **gross price/R performance without transaction-cost deductions**.

### 8.3 Observed-spread sensitivity

An observed-spread sensitivity scenario is permitted only after all of the following are explicitly documented and tested:

- the price-side convention (bid, ask, or midpoint);
- the point-to-price conversion;
- whether spread is applied at entry, exit, or as a full round trip;
- how the convention handles gaps and same-bar events;
- why the conversion is valid for the XAUUSD-P data.

Until the price convention and relevant metadata are verified, any spread-adjusted result must be labelled a **scenario**, not verified execution cost.

### 8.4 Hypothetical cost scenarios

Additional cost scenarios are permitted only when explicitly labelled as assumptions. They must not be presented as verified broker costs or as evidence of net profitability.

Do not invent commission, financing, slippage, margin, or account-currency costs.

### 8.5 Reporting rule

Every result must state:

- which costs were applied;
- which inputs were verified;
- which are scenarios;
- that gross/R results are not account returns;
- that no net-profitability claim is made.

---

## 9. Risk-State Simulation

### 9.1 Enforced in Phase E

The backtest can enforce deterministic research controls:

- maximum two active strategy positions;
- maximum three new entries per 00:00 UTC day;
- loss cooldown of 16 completed M15 bars after one loss;
- 32 completed M15 bars after two consecutive losses;
- duplicate-trigger prevention;
- no pyramiding, averaging down, martingale, recovery sizing, or fixed-lot behavior.

### 9.2 Not enforceable without monetization

The 3% daily account-equity loss limit cannot be evaluated as an account rule because:

- equity is not supplied by the research engine;
- monetary position size is blocked;
- contract economics and costs are unverified;
- daily P&L in account currency is unavailable.

Phase E must report daily R losses and a clearly labelled `daily_loss_limit_status=not_evaluable_monetary`. It must not claim that the 3% account limit was enforced.

### 9.3 State behavior

- Missing loss timestamps must fail closed.
- A cooldown may be expired only when a valid timestamp or persisted cooldown expiry establishes that it has expired.
- State is in-memory during a run; no persistence is added.
- The run manifest records initial state and every transition.

---

## 10. News and Session Treatment

### 10.1 News

No economic-calendar integration exists and none is authorized in this phase. The baseline replay therefore uses:

```text
research_mode=True
news_unaware=True
news_context=unavailable
```

Consequences:

- the news point is unavailable;
- no 30-minute pause can be verified;
- results cannot claim news-filter compliance;
- no “no news” inference is permitted.

If a verified calendar is supplied later, it is a separate data-integration approval.

### 10.2 Sessions

The research session mode uses the IANA London/New York overlap with:

```text
session_calendar_verified=False
```

It does not verify XAUUSD-P holidays, broker break periods, or actual session availability. Results must state that the IANA overlap is a research filter, not a verified instrument calendar.

---

## 11. Required Reporting

Every valid run must report, by split and overall:

- decision count and rejection count by rejection code;
- candidate count, filled trade count, and unclosed position count;
- gross R results;
- average R, median R, expectancy in R, profit factor in R;
- gross profit/loss in R;
- maximum drawdown in R;
- win rate, average win/loss in R, reward/risk distribution;
- trade duration in M15 bars and holding time;
- exposure (position-bars);
- confidence-tier results;
- long/short results;
- development/validation/holdout results separately;
- entry-decision period, exit period, later-period-data requirement, and open/truncated status for every trade;
- yearly results where meaningful;
- daily trade-count distribution;
- no-trade days;
- daily R drawdown and a clearly marked non-monetary daily-loss section with `daily_loss_limit_status=not_evaluable_monetary`;
- rejected-setup distribution;
- rule-compliance statistics;
- cost scenario results;
- data-quality and no-look-ahead checks;
- source checksums, strategy version, parameter hash, harness version and command;
- limitations and inconclusive status where sample size is inadequate.

Monetary return, annualized return, account risk, notional, margin, commission, financing and net profitability must be marked **unavailable** until contract economics and sizing are verified. They must not be replaced with fabricated values.

---

## 12. Test Plan for Phase E Implementation

No tests are added in this planning phase. Before a backtest is authorized, the implementation test suite must include:

### 12.1 Data and point-in-time tests

- checksum and row-count verification for all source files;
- rejected files fail closed;
- monotonic, unique, valid OHLC;
- no unfinished candles;
- D1/H4/M15 alignment;
- warm-up sufficiency;
- gap preservation and no synthetic bars;
- exact split boundaries;
- D1 2010–2026 overlap reconciliation.

### 12.2 Look-ahead tests

- append future D1/H4/M15 bars and prove the earlier decision record is byte-identical;
- append a future H4 bar that would create or change a pivot; earlier decision unchanged;
- change future ATR; earlier zone boundaries unchanged;
- future news snapshot does not alter earlier decision;
- next-bar non-open fields do not affect entry;
- decision replay with a prefix equals replay with the full dataset.

### 12.3 Strategy-behavior tests

- every replay decision equals a direct Phase D call on the same prefix;
- all candidate fields and rejection reasons are preserved;
- no rule is reimplemented outside `xauusd_p.py`;
- score and confidence reconstruct exactly;
- directional zones and merged boundaries remain Phase D behavior;
- symbol substitution fails.

### 12.4 Fill-engine tests

- gap through stop;
- gap through TP1/TP2;
- same-bar stop and target: stop-first;
- both targets on one bar: one target only;
- TP2 inactive before TP1;
- partial 50/50 arithmetic;
- breakeven after TP1;
- entry at next open, not trigger close;
- no fill when next bar missing;
- no fill beyond data cutoff;
- open/truncated positions reported separately.

### 12.5 Risk-state tests

- max concurrent positions;
- max daily entries;
- 16/32-bar cooldown;
- incomplete cooldown state fails closed;
- duplicate signal prevention;
- no pyramiding or averaging;
- daily loss marked not-evaluable-monetary;
- state transitions match the recorded trade lifecycle.

### 12.6 Cost tests

- missing spread does not become zero;
- observed spread scenario applied consistently;
- cost deductions are reported in price/R, not currency;
- scenario labels cannot be mistaken for verified costs.

### 12.7 Reporting/integrity tests

- deterministic output ordering;
- run manifest and checksums;
- two identical runs produce identical record hashes;
- holdout cannot be rerun with a changed configuration without a new manifest;
- no execution or broker import;
- no source-data mutation;
- rejected decisions are not dropped from diagnostics.

### 12.8 Integration checks

- synthetic end-to-end fixture through decision, fill, partials, and reporting;
- offline run against the saved Phase B data with checksums;
- full repository test suite;
- compile checks;
- `git diff --check`;
- execution-safety scan.

---

## 13. Acceptance Criteria

A backtest run is **valid** only if every required gate below passes. Otherwise its status is **inconclusive**, not a trading conclusion.

### 13.1 Data quality gates

- DQ-1: all source checksums match the manifests.
- DQ-2: row counts, schema and instrument identity match the expected XAUUSD-P datasets.
- DQ-3: timestamps are ordered, unique, aware, and on native grids.
- DQ-4: OHLC values are valid and no unfinished candles are used.
- DQ-5: D1/H4/M15 overlap is confirmed and warm-up requirements are met.
- DQ-6: gaps are preserved and never filled synthetically.
- DQ-7: no source file or manifest is modified by the run.
- DQ-8: missing/ambiguous cost data is reported, never assumed.
- DQ-9: the 2010–2026 warm-up D1 series is reconciled against the authoritative 2024–2026 D1 series before combining; any discrepancy is investigated and documented.

### 13.2 Strategy-behavior gates

- SB-1: every decision is produced by the accepted Phase D strategy.
- SB-2: no future data changes an earlier decision record.
- SB-3: decisions are deterministic across repeated runs.
- SB-4: zone, score, rejection and candidate fields are recorded.
- SB-5: direction, news, session and risk filters remain mandatory.
- SB-6: no symbol substitution, rule duplication, or parameter tuning occurs.

### 13.3 Backtest-integrity gates

- BI-1: entry is at the next M15 open.
- BI-2: stop-first governs ambiguous bars.
- BI-3: gap-stop and gap-target rules are applied exactly.
- BI-4: TP1, TP2, partials and breakeven are sequential and arithmetically consistent.
- BI-5: no impossible fill, negative quantity, or duplicate trade.
- BI-6: entry counts, concurrent positions and cooldowns are enforced.
- BI-7: daily account-loss compliance is marked unavailable, not assumed; daily R drawdown is reported separately.
- BI-8: trades are attributed to the split containing the entry decision timestamp; split cutoffs are hard; holdout is untouched.
- BI-9: every candidate/trade/rejection reconciles to a decision record.
- BI-10: open/truncated trades are excluded from closed-trade metrics and reported.
- BI-11: every trade records entry-decision period, exit period, later-period-data requirement, and open/truncated status.
- BI-12: entries whose next-bar entry time falls after the split cutoff are marked `entry_boundary_excluded` and are not included in the primary split sample.

### 13.4 Reporting gates

- RP-1: all required metrics and limitations are present.
- RP-2: monetary fields are marked unavailable rather than fabricated.
- RP-3: cost scenarios are labelled verified/unverified.
- RP-4: source checksums and run manifest are reproducible.
- RP-5: no profitability or execution-readiness claim is made.
- RP-6: fewer than 30 closed trades per split is explicitly labelled inconclusive under the preliminary sample-size gate.
- RP-7: results meeting the 30-trade threshold remain descriptive and include limitations and appropriate uncertainty analysis.

### 13.5 Sample-size gate

The owner-approved **preliminary minimum reporting threshold is 30 closed trades per split**.

- Fewer than 30 closed trades: the split result is labelled **inconclusive** under the preliminary sample-size gate.
- At least 30 closed trades: the result remains descriptive and must include limitations and appropriate uncertainty analysis.
- Thirty closed trades is not proof of statistical significance, reliable expectancy, or profitability.
- Strategy rules must not be changed to manufacture a larger sample.

---

## 14. Prerequisites Before Phase E Implementation

The following owner decisions are now **resolved and recorded**:

- Phase E covers XAUUSD-P only. GBPJPY-P is a separate future research workstream and is not part of this plan.
- As-of zone recomputation is approved. Prior decision records and zone snapshots are preserved; no immutable zone identities or persistent zone registry are introduced.
- The primary baseline is gross price/R without transaction-cost deductions.
- An observed-spread sensitivity is permitted only after its price-side convention, point-to-price conversion and round-trip application are documented and tested.
- Hypothetical additional cost scenarios are permitted only when explicitly labelled as assumptions; commission, financing, slippage, margin and account-currency costs are not invented.
- Daily R drawdown is reported, and the 3% account-equity daily-loss limit is marked `not_evaluable_monetary`.
- Thirty closed trades per split is the preliminary minimum reporting threshold; it is not proof of statistical significance, expectancy, or profitability.
- Trades are attributed by entry decision timestamp. Split-boundary records include entry-decision period, exit period, later-period-data requirement, and open/truncated status. Primary split metrics use hard cutoffs and do not use later-split data.

Remaining prerequisites:

1. Owner approval of the updated planning document.
2. Completion and acceptance of the source-level Phase D verification.
3. Confirmation that the Phase D strategy version and parameters are frozen for the run.
4. Confirmation that saved data checksums and manifests are unchanged.
5. Completion of the documented warm-up D1 reconciliation between the 2010–2026 and 2024–2026 datasets; any discrepancy must be investigated and documented, never silently combined.
6. Documentation and tests for the observed-spread convention before any spread-adjusted result is produced.
7. Agreement that Phase E produces R/price research metrics only, not monetary returns.
8. Separate explicit authorization before any implementation or backtest execution, including the holdout run.

---

## 15. Remaining Blockers

- Contract-size/tick-value/account-currency/margin verification.
- Commission, financing/swap, slippage and stop-level verification.
- Partial-exit volume feasibility.
- Verified XAUUSD-P session and holiday calendar.
- Verified economic calendar and historical coverage.
- Persistent risk/signal state and restart reconciliation.
- Daily account-loss boundary and equity source.
- Cost-adjusted breakeven.
- Observed-spread price-convention verification and tests.
- Downstream `TradeSignal` contract for TP2 and blocked sizing.
- Statistical uncertainty and interpretation of the 30-trade preliminary threshold.
- Phase E implementation, holdout execution and backtest execution approval.

---

## 16. Recommendation

**Phase E planning is ready for owner review.** The proposed architecture evaluates the accepted Phase D strategy point-in-time, preserves chronological splits, defines conservative fills and gaps, separates verified from unverified costs, enforces non-monetary research controls, and provides explicit data, strategy and backtest integrity gates.

**No Phase E implementation or backtest execution is authorized by this document.** Implementation requires completion of the remaining prerequisites and a separate explicit authorization. The owner decisions recorded above do not authorize implementation.

No profitability claim is made.
