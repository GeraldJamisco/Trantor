# Trantor Final Design Acceptance Report

- **Design status:** Accepted by the owner. WP1 profile/protocol/runtime safety, WP2 canonical research-record integration, and WP3 backtester hardening are implemented and verified. The authoritative pre-run checkpoint passed; the full historical experiment has not been run and awaits owner authorization. Paper/demo/live execution remains disabled.
- **Active instrument:** `XAUUSD-P` only.
- **Design evidence:** repository source/tests/docs inspected; baseline suite `450 passed`; no full historical backtest was run.
- **Design package:** `docs/architecture/`.

## 1. What already exists?

| Area | Existing capability | State |
|---|---|---|
| Domain models | Instruments, observations, signals, risk, orders, order state, positions, trade records | Implemented and tested |
| Configuration | Strict typed JSON loaders, schema validation, no silent defaults | Implemented for legacy GC/MGC; XAUUSD-P profile missing |
| Safety | Paper-only gate, hard ceilings, order authorizer, duplicate ID guard, redaction | Implemented for existing safety boundary; execution disabled |
| Broker | `Broker` protocol and fail-closed `DisabledBroker` | Implemented; no submission adapter |
| Market data | Typed provider/service contracts, explicit failure statuses, COMEX GC/MGC sessions | Implemented |
| MT5 | Read-only `XAUUSD-P` OHLC provider and metadata | Implemented; no account/order access |
| Legacy scanner | GC/MGC research analysis and replay | Implemented; not on XAUUSD-P strategy path |
| XAUUSD-P strategy | Approved multi-timeframe strategy and research-only candidate | Implemented and tested |
| Research variant | Explicit session-isolated/news-unaware variant | Implemented and tested |
| Research record | Canonical `trantor.research_decision` v1.0 serializer/validator | Implemented and tested |
| Backtester | Deterministic gross R replay, fills, risk, splits, reports | Implemented; full experiment not run |
| Persistence | None | Gap |
| Monitoring/Telegram | Placeholder only | Gap |
| Scheduler/ops | None | Gap |
| Paper/demo/live execution | None | Gap/blocked |

## 2. What is incomplete or defective?

1. **Instrument/config conflict:** config and legacy COMEX components reject or do
   not recognize `XAUUSD-P`; README still describes GC/MGC.
2. **Strategy protocol conflict:** current `Strategy` protocol does not match the
   actual D1/H4/M15 `StrategyDecision` API.
3. **Signal incompleteness:** `TradeSignal` omits TP2/partial/breakeven/readiness
   and cannot be the execution source.
4. **Risk-model conflict:** research R-risk and monetary account risk share no
   safe common approval object.
5. **Session conflict:** COMEX GC/MGC calendar cannot be applied to XAUUSD-P.
6. **Runtime completion gap:** `MarketObservation` carries open time, not an
   explicit completed-bar marker.
7. **Persistence/recovery gap.**
8. **No paper/demo/live adapter, reconciliation, scheduler, monitoring or ops
   interface.**
9. **Full historical experiment absent.**
10. **Documentation drift:** Phase C/D/E statuses describe earlier states.

## 3. What will the finished system contain?

A single-process Windows Python system with:

- typed XAUUSD-P configuration and instrument profile;
- read-only MT5/historical market-data provider with completed-bar semantics;
- one approved strategy implementation and explicit research variant;
- canonical decision records and deterministic research reports;
- separate research R-risk and account-risk engines;
- disabled-by-default execution boundary with paper/demo adapters only after
  gates;
- position manager and broker reconciliation;
- SQLite persistence and recovery;
- session/news provider interfaces with fail-closed behavior;
- read-only operations status and outbound Telegram notifications;
- structured logging, testing and release gates.

## 4. How will components interact?

Research:

```text
Historical/MT5 completed data
  -> Historical view / MarketDataService
  -> StrategyDecision
  -> canonical record
  -> research risk/fill simulator
  -> reports
```

Execution:

```text
StrategyDecision
  -> account risk (verified economics)
  -> order authorizer
  -> paper/demo/live adapter
  -> broker state/reconciliation
  -> persistence/notifications
```

No strategy decision can bypass account risk, authorizer, idempotency, or
reconciliation. No research component imports execution or safety.

## 5. Which decisions are already approved?

- XAUUSD-P is authoritative; GC/MGC and other instruments are not substitutes.
- Strategy rules and thresholds are unchanged.
- Research-only news-unaware/session-isolated variant is authorized for research.
- Dataset-time coordinate with unverified absolute UTC provenance is accepted
  only with labels and no compliance claims.
- As-of zone recomputation.
- Gross price/R baseline with execution costs excluded.
- Development 2024, validation 2025, holdout 2026 through 2026-10-21.
- Entry-decision split attribution and continuous state across splits.
- Conservative stop-first/one-target-per-bar fill assumptions.
- 30 closed trades per split as a preliminary inconclusive threshold.
- Research-record contract v1.0 and Phase E backtester implementation.
- No live/demo execution, monetary sizing, broker orders, or live readiness.

## 6. Which decisions remain unresolved?

See `known_limitations_register.md` D-01–D-13. The design-blocking set is:

1. XAUUSD-P config/profile migration and legacy GC/MGC isolation.
2. `ResearchStrategy` protocol adoption.
3. Execution mode model with disabled default.
4. Session/news provider evidence.
5. Persistence schema and paper model.
6. Spread convention.
7. Account economics/volume/margin.
8. Full experiment authorization.
9. Structural identity limitation acceptance.
10. Telegram command scope (read-only recommended).

## 7. Which unresolved items block historical research?

- Full experiment authorization (WP4) — gross research implementation exists,
  but running the full split/holdout requires explicit owner instruction.
- Absolute-UTC/session/news *claims* are blocked; a labelled gross dataset-time
  experiment can proceed without claiming them.
- Cost-adjusted results are blocked; gross R remains permitted.
- XAUUSD-P config/profile correction is required before integrated research
  (WP1), though the standalone backtester already loads the saved datasets.
- Identity provenance limits auditable origin claims, not gross fill mechanics.

## 8. Which items block demo trading?

- Verified XAUUSD-P session/holiday and economic calendars.
- Verified contract economics, tick/volume/margin/commission/financing/slippage.
- Account equity/daily-loss source.
- Durable persistence, idempotency and restart recovery.
- Paper adapter validation, then MT5 demo adapter.
- Position/order reconciliation and operational monitoring.
- Explicit owner authorization.

## 9. Which items block live execution?

All demo blockers plus:

- independent live execution/security review;
- live account/broker evidence and stop-level rules;
- verified cost and account-risk model;
- monitored operational runbook and alerting;
- separate live configuration and explicit live authorization;
- evidence that the demo path matches live semantics;
- acceptance that no backtest result proves profitability.

## 10. Exact implementation order

1. Owner design acceptance; freeze baseline (WP0).
2. XAUUSD-P profile/config/strategy protocol correction (WP1).
3. Research-record/strategy integration hardening (WP2).
4. Historical backtester completion and full-run readiness (WP3).
5. Full historical experiment, separate authorization (WP4).
6. Runtime market-data hardening (WP5).
7. Persistence/recovery (WP6).
8. Research/paper risk-state integration (WP7).
9. Paper trading simulation (WP8).
10. Session/news providers (WP9; contracts may start earlier).
11. Execution interfaces and disabled adapters (WP10).
12. MT5 demo validation (WP11, external facts + authorization).
13. Monitoring/operations/Telegram (WP12; read-only).
14. Live-readiness assessment, separate authorization (WP13).

WP5–WP8 can proceed in parallel after WP1, but the research path must remain
isolated from execution.

## 11. How will completion be measured?

Each work package passes:

- documented interface/contract implementation;
- unit/integration/fault/recovery tests;
- deterministic records/config/data fingerprints;
- safety/import boundary checks;
- updated limitations;
- explicit owner/governance gate.

Phase gates:

- Research: focused/full tests, dataset checksums, small smoke, gross labels.
- Paper: paper adapter/reconciliation/recovery tests, no external orders.
- Demo: MT5 demo evidence, verified economics/calendars, owner authorization.
- Live: all prior plus independent review and separate live authorization.

No completion claim may rest on a positive backtest or a Telegram message.

## 12. Are there any contradictory requirements?

Yes. These are current conflicts, not new requirements:

1. README/config/GC-MGC COMEX scope vs approved XAUUSD-P strategy.
2. `Strategy` protocol vs actual `StrategyDecision` multi-series API.
3. `TradeSignal` as strategy output vs complete management requirement.
4. Paper/live `Safety`/monetary risk vs research R risk.
5. COMEX calendar vs XAUUSD-P unverified broker instrument.
6. Existing positions/orders protocol vs missing persistence/reconciliation.
7. Phase C/D documents saying later phases do not exist vs current backtester.
8. `Rule/gold.json` placeholder vs approved strategy parameters.

The design resolves these by separation, not by changing approved strategy rules.

## 13. Smallest path to first trustworthy historical backtest

1. Accept this design and resolve WP1 profile/protocol conflict.
2. Freeze strategy version/parameters, datasets, backtest configuration and
   report schema.
3. Run the existing focused backtester tests and full repository suite.
4. Run the small smoke; verify dataset checksums, completed-bar timing,
   future-data invariance and canonical records.
5. Obtain explicit owner authorization for the full development/validation/
   holdout run.
6. Report only gross R, dataset-time labels, limitations, and 30-trade
   inconclusive status.
7. Do not tune on the holdout.

## 14. Smallest path to a fully integrated demo-ready system

1. Complete WP1–WP8: profile/config, research integration, persistence,
   research/paper risk, paper adapter and recovery.
2. Verify session/news and economics.
3. Implement demo adapter and isolated demo config.
4. Validate order lifecycle, partial fills, stops/targets, reconciliation and
   restart on MT5 demo.
5. Add read-only operations/monitoring/alerts.
6. Run release acceptance; request separate live authorization only after demo
   evidence.

## Final design review and recommendation

**Design verdict: READY FOR OWNER REVIEW, NOT YET IMPLEMENTATION-AUTHORIZED.**

The design is coherent and implementable if the owner accepts the separation
decisions above. The highest-priority corrections are:

1. XAUUSD-P profile/config and legacy isolation.
2. Strategy protocol correction.
3. Explicit execution mode/disabled barrier.
4. Separate research vs account risk.
5. XAUUSD-P session/news provider boundary.

No implementation, backtest, order, account change, dataset change, or execution
path was performed by this design task.

**Stop here and present this package for review.**
