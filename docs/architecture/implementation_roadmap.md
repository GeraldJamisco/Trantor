# Trantor Implementation Roadmap

This roadmap is dependency-ordered. The owner accepted the design and WP1
profile/protocol/runtime safety, WP2 canonical research-record integration, and
WP3 backtester hardening are implemented. The authoritative pre-run checkpoint
passed, including mandatory canonical/linkage validation and deterministic
small-window replay. The full historical experiment (WP4) has not been run and
remains blocked on explicit owner authorization. Each package has an
owner-visible completion gate and can proceed only when its dependencies are
satisfied.

## 1. Work-package overview

| WP | Package | Depends on | Can start now? | Completion gate |
|---|---|---|---|---|
| WP0 | Design acceptance and repository baseline freeze | none | yes, after review | signed design package; baseline test/config inventory |
| WP1 | XAUUSD-P profile and interface correction | WP0 | yes | config/profile/strategy protocol tests; no threshold change |
| WP2 | Research record/strategy integration hardening | WP1 | yes | canonical record, variant, replay equality tests |
| WP3 | Historical backtester completion and full-run readiness | WP2 | yes | all focused/full tests; small smoke; full-run authorization package |
| WP4 | Historical experiment execution | WP3 + owner instruction | no | reproducible reports, splits, limitations; no holdout tuning |
| WP5 | Runtime market-data hardening | WP1 | yes | MT5 provider/freshness/completion/reconnect tests |
| WP6 | Persistence and recovery | WP1 | yes | SQLite schema, restart/recovery, record immutability |
| WP7 | Research/paper risk-state integration | WP2, WP6 | yes | R state, paper risk, reconciliation contracts |
| WP8 | Paper trading simulation | WP7 | yes | paper adapter lifecycle, no external orders, restart |
| WP9 | Session/news provider integration | WP1 | partly: contracts; provider later | research session labels; fail-closed production/news |
| WP10 | Execution interfaces and disabled adapters | WP8, WP9 | partly | Broker contract, authorizer, idempotency, no live |
| WP11 | MT5 demo validation | WP9, WP10 + external evidence | no | demo adapter validation, reconciliation, economics |
| WP12 | Monitoring, operations, alerts | WP6, WP7 | yes | read-only status, Telegram, redaction, delivery tests |
| WP13 | Live-readiness assessment | WP11, WP12 + explicit owner | no | final release review; live authorization separate |

The historical research sequence remains before live infrastructure. WP5–WP8
may proceed in parallel after WP1, but no paper/execution code may be imported by
the research path.

## 2. Detailed packages

### WP0 — Design acceptance and baseline freeze

Inputs:

- this design package;
- current repository and test baseline;
- approved strategy/research documents.

Work:

- owner review of design, conflicts and unresolved decisions;
- freeze repository revision, strategy version, parameters, data checksums;
- record baseline tests (`450 passed` at design time);
- confirm no full historical run occurred.

Completion criteria:

- design accepted;
- blocking conflicts/decisions resolved or explicitly deferred;
- baseline manifest created.

### WP1 — XAUUSD-P profile and interface correction

Work:

- introduce `/instruments` profile for XAUUSD-P (`UNVERIFIED_GOLD`, no COMEX);
- preserve GC/MGC legacy paths;
- extend config allow-list without mixing universes;
- add `ResearchStrategy` protocol matching actual `evaluate`;
- add explicit runtime/execution mode with disabled default;
- update README/config docs after tests.

Tests:

- XAUUSD-P config accepted;
- GC/MGC legacy tests unchanged;
- config rejects mixed/ambiguous instrument;
- old `Strategy` protocol not selected for XAUUSD-P;
- execution mode default disabled.

Completion criteria:

- configuration resolves the only active strategy instrument;
- no strategy rules changed;
- legacy tests pass.

### WP2 — Research record/strategy integration hardening

Work:

- verify canonical record integration against actual strategy decisions;
- preserve `StrategyDecision` via decision ID/hash;
- enforce variant/filter/news labels;
- add replay equality test: backtest decision == direct strategy decision;
- add record store contract for decisions.

Tests:

- approved/research decisions distinct;
- identity/filter/news finite/compliance checks;
- rejection record;
- deterministic canonical JSON;
- no candidate/TradeSignal as sole source.

Completion criteria:

- every candidate/rejection has an auditable canonical record;
- no serialization/integrity regression;
- tests pass.

### WP3 — Historical backtester completion and full-run readiness

Work:

- finalize data loader identity/checksums, prefix views, fill and risk state;
- report validation and deterministic outputs;
- freeze configuration and report schema;
- add deployment/run instructions;
- run small smoke only;
- prepare full-run command and expected limitations.

Tests:

- current `tests/test_research_backtest.py` plus stronger data/fill/invariance;
- full suite, compile, diff;
- no execution/order imports;
- source data read-only.

Completion criteria:

- no known correctness defect;
- dataset identity/checksum verified;
- focused/full tests pass;
- owner approves full experiment execution separately.

### WP4 — Historical experiment execution

Gate: WP3 + explicit owner instruction.

Work:

- run development/validation/holdout according to plan, once;
- preserve reports/manifests/checksums;
- report gross R only;
- label <30 closed trades inconclusive;
- do not tune on holdout;
- record limitations.

Completion:

- reproducible report artifacts;
- holdout not reused for tuning;
- owner accepts evidence status.

### WP5 — Runtime market-data hardening

Work:

- complete/reconnect/freshness behavior;
- explicit `BarCompletion` or equivalent sidecar;
- MT5 read-only lifecycle tests/fault injection;
- runtime MT5 prefix equals historical view for same data;
- no account/order calls.

Completion:

- live observation can feed strategy only with complete/valid bars;
- provider errors/stale data are explicit;
- no execution side effect.

### WP6 — Persistence and recovery

Work:

- SQLite WAL schema/migrations;
- decision/trade/position/risk/order/reconciliation tables;
- immutable research record store;
- restart/load/recovery tests;
- redaction/access controls.

Completion:

- research records reproducible;
- paper/live state can recover without duplicate orders;
- unknown state disables new orders.

### WP7 — Research/paper risk integration

Work:

- persist `ResearchRiskState`; daily reset; cooldown/concurrency/duplicate;
- separate `AccountRiskApprover` skeleton with missing economics fail-closed;
- daily R/drawdown/exposure reports;
- paper account state only with declared paper model.

Completion:

- R risk controls tested end to end;
- monetary risk unavailable, not zero;
- no R-to-order shortcut.

### WP8 — Paper trading simulation

Work:

- `PaperBroker` with explicit fill model and idempotency;
- paper position lifecycle, stop/target/partial/breakeven;
- reconciliation and restart;
- notifications labelled paper.

Completion:

- paper orders are never external;
- restart cannot duplicate;
- paper P&L claims use declared model only.

### WP9 — Session/news providers

Work:

- provider contracts, freshness/coverage/version;
- XAUUSD-P research session adapter (IANA overlap, unverified);
- no COMEX mapping;
- news fail-closed and research-unaware;
- provider failure tests.

Completion:

- research results labelled unverified;
- demo/live remain blocked without verified providers and owner evidence.

### WP10 — Execution interfaces and disabled adapters

Work:

- retain `Broker`/`DisabledBroker`;
- order authorizer mode/provenance/idempotency;
- no live adapter enabled;
- mock broker contract tests;
- reconciliation contract.

Completion:

- only approved mode/risk reaches an adapter;
- disabled default and unknown-state fail closed;
- strategy/research import scan proves isolation.

### WP11 — MT5 demo validation

Gate: verified economics, volume/session/news, persistence, risk, execution.

Work:

- separate demo adapter and config;
- explicit owner authorization;
- demo order lifecycle, partial fills, reconciliation, restart;
- no live credentials/settings changes.

Completion:

- demo evidence recorded;
- reconciliation matches broker;
- no claim of live readiness.

### WP12 — Monitoring, operations, alerts

Work:

- read-only status/ops interface;
- Telegram adapter with redaction/dedup/retry;
- daily brief, decisions, rejections, paper/demo status, errors, risk events;
- delivery failure handling.

Completion:

- notification cannot execute or confirm;
- owner can read health/mode/freshness/R/positions;
- secrets redacted.

### WP13 — Live-readiness assessment

Gate: all prior gates + explicit owner authorization.

Work:

- final safety/account/operational review;
- live adapter decision separate from demo;
- release acceptance.

Completion:

- signed go/no-go;
- limitations and operational runbook;
- live only if separately authorized.

## 3. External-fact dependencies

| Fact | Needed for | Blocks |
|---|---|---|
| Broker MT5 epoch/UTC semantics | absolute timestamp/session claims | demo/live; not gross research labels |
| XAUUSD-P session/holiday calendar | session compliance | demo/live; research remains isolated/unverified |
| Historical economic calendar | news compliance | demo/live; research remains unaware |
| Spread unit/quote side/conversion | spread-adjusted research | cost-adjusted reports |
| Contract size/tick value/margin/commission/financing/slippage | monetary sizing | paper/live; paper model may be declared simulation only |
| Volume min/step/partial feasibility | partial execution | demo/live |
| Broker order/reconciliation semantics | execution | demo/live |
| Source dataset checksums/manifests | historical research | any run that claims dataset identity |

No fact may be inferred from a filename or a positive backtest.

## 4. Immediate vs blocked work

Immediately actionable after design acceptance:

- WP0, WP1, WP2, WP3, WP5 (read-only), WP6, WP7, WP8-paper-contract,
  WP9-contracts, WP12-read-only interfaces.

Blocked:

- full historical run (explicit owner execution instruction);
- any cost-adjusted result (spread evidence);
- session/news compliance claims (provider evidence);
- monetary sizing and demo/live (economics/broker evidence/authorization);
- full live release.

## 5. Completion measurement

A work package is complete only when:

1. contract implemented or documented as deferred;
2. unit/integration tests pass;
3. failure cases tested;
4. safety/import boundaries verified;
5. records/config/data fingerprints reproducible;
6. limitations updated;
7. owner/governance gate recorded.

A phase is complete when all package gates pass and the next phase has an
explicit, recorded authorization decision.
