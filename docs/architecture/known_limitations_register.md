# Trantor Known Limitations and Unresolved Decisions Register

This register is normative for design review. “Unresolved” means the repository
does not contain sufficient evidence to choose the answer safely. Do not infer a
fact from a filename, a passing synthetic test, or a gross backtest.

## 1. Blocking-stage legend

- **DESIGN** — must be decided before implementation begins.
- **RESEARCH** — affects the meaning/validity of historical research.
- **DEMO** — blocks MT5 demo validation.
- **LIVE** — blocks live execution/release.
- **COST** — blocks cost-adjusted results only; gross research may remain valid
  with labels.

## 2. Current design conflicts / defects

| ID | Conflict | Evidence | Smallest correction | Blocks |
|---|---|---|---|---|
| C-01 | README/config/scanners are GC/MGC COMEX; strategy/provider are XAUUSD-P | README; `config.py` allowed symbols GC/MGC; `market_data/gold.py` rejects non-futures; strategy uses XAUUSD-P | Add XAUUSD-P instrument profile/config path; isolate GC/MGC legacy; no COMEX mapping | DESIGN, implementation |
| C-02 | `Strategy` protocol returns `TradeSignal|None` from one series; actual strategy returns `StrategyDecision` from D1/H4/M15 | `strategy/__init__.py`; `xauusd_p.evaluate` | Add `ResearchStrategy` protocol; keep legacy protocol for compatibility | DESIGN, implementation |
| C-03 | `TradeSignal` lacks TP2/partials/BE/readiness | `models.py`; `xauusd_p` candidate conversion; Task 1 | Keep legacy signal; authoritative path uses `StrategyDecision`; no execution from signal | DESIGN, implementation |
| C-04 | Safety/monetary risk object vs research R | `safety.py`/`models.RiskAssessment`; `ResearchRiskState` | Separate `ResearchRiskTracker` and `AccountRiskApprover`; never map R to quantity | DESIGN, implementation |
| C-05 | `GoldSessionCalendar` is verified COMEX GC/MGC only; XAUUSD-P session unverified | `market_data/gold.py`; readiness review | Add XAUUSD-P research session provider; never pass UNVERIFIED_GOLD to COMEX calendar | DESIGN, implementation |
| C-06 | Runtime MT5 observation lacks explicit bar-completion field | `models.MarketObservation`; provider returns open time | Add `BarCompletion`/complete-at sidecar; require completed bars | DESIGN, implementation |
| C-07 | No persistence/recovery; `ResearchRiskState` caller-supplied | `phase_d` audit; no persistence module | Add SQLite state/record store and reconciliation before paper/demo | DESIGN, implementation |
| C-08 | Current docs/readiness statuses are from earlier phases | Phase C/D/E docs; README | Preserve as historical; current architecture package is the design map | Documentation review |
| C-09 | Research-record identity is structural, not provenance | Record contract/audit | Keep structural checks; do not claim origin; optional future signed/registry evidence | DESIGN acceptance |
| C-10 | Full historical experiment not run | Backtester implementation; smoke only | WP4 explicit owner execution instruction | RESEARCH |

## 3. External-fact limitations

| ID | Limitation | Current status | Impact | Needed evidence |
|---|---|---|---|---|
| L-01 | MT5 epoch/absolute UTC provenance | UNVERIFIED | Session/daily/split/news claims; not internal candle sequence | Broker/server timezone + known UTC mapping |
| L-02 | XAUUSD-P session/holiday calendar | UNVERIFIED/UNAVAILABLE | Demo/live session compliance; research variant only | Broker session/holiday specification |
| L-03 | Historical economic calendar | UNAVAILABLE | News safety/point/compliance | Verified calendar source/coverage/version |
| L-04 | Spread unit/quote side/point conversion | UNVERIFIED | Cost-adjusted results | Broker instrument spec, bid/ask basis, round-trip rule |
| L-05 | Contract size/tick value/value per price unit | SNAPSHOT ONLY | Monetary sizing/account P&L | Authoritative contract economics/account statement |
| L-06 | Volume min/step/partial feasibility | SNAPSHOT ONLY | TP1/TP2 execution size | Broker volume rules + partial tests |
| L-07 | Commission/financing/slippage/margin | UNAVAILABLE | Net profitability | Broker/account evidence |
| L-08 | Stop/freeze/execution constraints | UNAVAILABLE | Order safety | Broker specification |
| L-09 | Account equity/start-of-day loss | UNAVAILABLE | 3% account rule | Verified account feed/economics |
| L-10 | Broker order/fill/reconciliation semantics | UNAVAILABLE | Demo/live | Demo adapter evidence and broker docs |
| L-11 | D1/H4/M15 historical gaps | UNCLASSIFIED | Sample/interpretation | Verified XAUUSD-P holiday/session calendar |
| L-12 | 30 trades/split | PRELIMINARY | Inconclusive below threshold | Larger sample; no rule change |
| L-13 | Partial-exit volume feasibility | BLOCKED | Full management | Volume evidence |
| L-14 | Identity historical provenance | NOT ESTABLISHED | Audit claims | Signed records/registry/independent provenance |
| L-15 | Full historical holdout run | NOT EXECUTED | No performance evidence | Explicit owner instruction |

## 4. Decisions already approved

- XAUUSD-P is the active instrument; GC/MGC are not substitutes.
- Approved strategy rules/thresholds remain unchanged.
- Research-only session/news isolated variant is authorized for initial research.
- As-of zone recomputation is approved.
- Gross price/R baseline is the initial research treatment.
- Dataset-time coordinate is accepted only with explicit unverified labels.
- Development 2024 / validation 2025 / holdout 2026 through 2026-10-21.
- Trades attributed by entry decision timestamp; continuous state across splits.
- Conservative stop-first/one-target-per-bar fill conventions.
- 30 closed trades/split is a preliminary reporting threshold, not proof.
- Research-record contract v1.0 and Phase E backtester implementation are authorized.
- No live/demo execution, monetary sizing, or broker order submission is authorized.

## 5. Decisions still required

| Decision | Options / constraints | Blocks |
|---|---|---|
| D-01 XAUUSD-P config/profile migration | Add new profile while retaining legacy GC/MGC; no mixed universe | Design/implementation |
| D-02 Runtime strategy protocol | Add `ResearchStrategy`; do not break existing protocol | Design/implementation |
| D-03 Execution mode model | `disabled/research/paper/demo/live` or equivalent; default disabled | Design/implementation |
| D-04 Session provider | IANA-research mode vs verified provider; no COMEX mapping | Research labels/demo/live |
| D-05 News provider | Verified source/coverage vs research-unaware only | Demo/live; cost/point claims |
| D-06 Spread convention | Do not implement until evidence; gross remains | Cost-adjusted research |
| D-07 Account economics | Contract/tick/volume/margin/commission evidence | Demo/live |
| D-08 Persistence schema | SQLite WAL tables/retention/reconciliation | Paper/demo/live |
| D-09 Paper model | Fill/price/order semantics; no external broker | Paper |
| D-10 Monitoring commands | Read-only notifications only initially | Design/operations |
| D-11 Full experiment authorization | Owner runs development/validation/holdout once | Research |
| D-12 Identity provenance | Accept structural limitation or add external evidence | Audit/live governance |
| D-13 Legacy GC/MGC handling | Retain isolated vs retire; no XAUUSD-P mixing | Design/implementation |

## 6. Impact by stage

### Blocks design completion

- None if the owner accepts the proposed resolutions for C-01–C-07 and D-01–D-08.
- The design itself is complete enough to implement the gross research path, but
  the conflicts must be fixed before integration.

### Blocks gross historical research

- Full run authorization (D-11); gross baseline can otherwise be implemented.
- Absolute-UTC/session/news claims are blocked, but not the labelled gross
  dataset-time experiment.
- Spread/cost-adjusted results are blocked; gross results are allowed.
- Identity provenance is a claim limitation, not a gross fill blocker.

### Blocks paper trading

- Persistence/recovery (D-08).
- Paper adapter/model (D-09).
- Research/account risk separation (C-04).
- Execution mode (D-03).
- No external broker/order path.

### Blocks MT5 demo

- Verified session/news providers (D-04/D-05).
- Contract economics/volume/margin (D-07).
- Paper/demo execution/reconciliation (D-08/D-09).
- MT5 demo adapter/evidence and explicit owner authorization.
- Absolute timestamp provenance for session claims.

### Blocks live execution

- All demo gates.
- Account-risk controls and verified economics.
- Durable idempotency/recovery/reconciliation.
- Independent security/operational review.
- Explicit live authorization and separate live configuration.
- No claim of profitability or execution readiness.

## 7. Smallest safe paths

### First trustworthy gross historical backtest

1. Accept design package.
2. Correct XAUUSD-P profile/config and strategy protocol (WP1).
3. Harden record/backtest integration and run focused/full tests (WP2–WP3).
4. Freeze configuration/data/report schema.
5. Run a small smoke; owner authorizes full development/validation/holdout run.
6. Keep gross labels, dataset-time limitations, no cost-adjusted claims.

### Fully integrated demo-ready system

1. WP1–WP8 (research, persistence, paper).
2. Verify session/news and economics (D-04/D-05/D-07).
3. Implement demo adapter only after explicit authorization.
4. Validate demo orders, partial fills, reconciliation, restart.
5. Implement monitoring/ops/read-only Telegram.
6. Run release acceptance and request separate live authorization.

## 8. Review instructions

The owner should accept, reject, or defer each of C-01–C-10 and D-01–D-13 before
implementation. An unresolved item must not be silently converted into an
implementation assumption. Any change to strategy thresholds/entry/stop/target
rules requires a separate strategy review, not an architecture workaround.

---

## 9. Status update — 2026-11-04 (completion programme)

Evidence and details: `docs/operations/implementation_log.md`,
`docs/operations/acceptance_report.md`.

| ID | Previous status | Status now | Evidence |
|---|---|---|---|
| C-01 | conflict | **Resolved for runtime**: runtime and gate are XAUUSD-P-only; GC/MGC legacy untouched | `runtime/`, `execution/gate.py` |
| C-02/C-03 | conflict | **Resolved**: runtime consumes `StrategyDecision`; `TradeSignal` used only for `signal_id` | `runtime/engine.py` |
| C-04 | conflict | **Resolved**: R tracker (`RiskTracker`) separate from monetary sizing (`size_position`, blocked without verified economics) | `runtime/risk.py` |
| C-06 | conflict | **Resolved**: completed vs forming bars identified against the measured broker clock | `runtime/candles.py` |
| C-07 | conflict | **Resolved**: SQLite WAL store, restart recovery, reconciliation | `runtime/store.py`, `engine.reconcile` |
| L-01 | UNVERIFIED | **Measured**: broker server = UTC+2 on 2026-11-04; historical data consistent with server = New York + 7 h (US DST). Rule inferred, not broker-confirmed; summer offset (+3) not yet observed live | `runtime/timebase.py`, `research/experiments.py` |
| L-02 | UNAVAILABLE | Still unverified. MT5 Python API exposes no session times. Daily break 22:00–23:00 UTC (winter) and Monday 01:00-server weekly open observed in data | broker verification report |
| L-03 | UNAVAILABLE | **Official-source forward calendar compiled** (BLS CPI, BLS Employment Situation, Fed FOMC; 2026-11-04 → 2027-12-10), `owner_confirmed=false` → still unverified for the strategy. No historical calendar | `config/news_calendar.json` |
| L-04 | UNVERIFIED | **Observed live**: symbol spread = (ask−bid)/point; bars built from bid (single sample); typical spread 18 points ($0.18) | broker verification report |
| L-05/L-06 | SNAPSHOT | **Observed live and internally consistent** (contract 100, tick 0.01 = $1/lot, `order_calc_profit` agrees; vol 0.01–50 step 0.01). Not owner-confirmed → monetary sizing still blocked | broker verification report |
| L-07 | UNAVAILABLE | Swap observed (−73.6 / +30.7 points, triple Wednesday); commission $0.00 on 2 historical deals (insufficient); slippage unknown | broker verification report |
| L-08 | UNAVAILABLE | **Observed**: market execution, FOK/IOC filling, stops level 10 points, freeze level 0 | broker verification report |
| L-09 | UNAVAILABLE | Paper equity model makes the 3% rule evaluable in paper; real account equity not used | `runtime/risk.py` |
| L-10 | UNAVAILABLE | Unchanged: no demo adapter exists; demo/live refused by the gate | `execution/gate.py` |
| L-15 | NOT EXECUTED | Executed 2026-11-02 (Phase E) | Phase E artifacts |

New limitations recorded by this programme:

| ID | Limitation | Impact |
|---|---|---|
| L-16 | Paper/shadow exits use the M15 bar model (stop-first), not ticks | paper results are a forward test of the research fill model, not of broker execution |
| L-17 | Paper P/L is `R × risk amount`; no volume rounding, spread, commission or swap | paper results are gross |
| L-18 | Runtime database grows ~5.6 KB per decision (~134 MB/year) | archival policy needed for long operation |
| L-19 | Live shadow observation in this programme covered one trading day (10:45–16:40 UTC), not multiple sessions | shadow acceptance not yet complete |
| L-20 | Runtime working-set memory grew 15→30 MB over ~5 h; plateau unconfirmed | watch in multi-day run |
