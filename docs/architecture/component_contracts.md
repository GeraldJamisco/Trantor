# Trantor Component Contracts

Each contract is normative for the implementation phase. Names marked
**proposed** do not yet exist in the repository and require owner approval via
this design. Existing names remain unless a documented conflict requires a
minimal correction.

For every component: purpose, inputs, outputs, dependencies, persistent state,
failure modes, safety constraints, tests, completion criteria.

---

## A. Configuration and Identity

### A.1 Purpose

Provide typed, versioned, validated runtime configuration; resolve the
`XAUUSD-P` instrument profile; separate research/paper/demo/live modes; expose
configuration identity; make execution impossible by accident.

### A.2 Inputs

- JSON configuration files under `config/` and strategy/profile files.
- Environment variables for secrets only.
- Explicit CLI overrides limited to non-secret operational values.
- Repository-controlled defaults and schema version.

### A.3 Outputs

- Immutable `RuntimeSettings` and `InstrumentProfile` objects.
- Validated execution-mode and provider configuration.
- Configuration fingerprint and visible effective settings.
- Redacted effective-config snapshot for reports/logs.

### A.4 Dependencies

- `src/trantor/config.py` (extend).
- `src/trantor/models.py` (`AssetClass`, `TradingMode`).
- A new `instruments` profile boundary (proposed).

### A.5 Persistent state

- Configuration files are source input, not mutable state.
- Run manifest stores config fingerprint and schema version.
- No secret value is persisted outside its environment source.

### A.6 Failure modes

| Failure | Behavior |
|---|---|
| Missing/malformed/duplicate-key JSON | `ConfigurationError`; no fallback |
| Unknown schema version | reject; no migration guess |
| Unsupported instrument | reject before provider/strategy initialization |
| `XAUUSD-P` mixed with GC/MGC universe | reject |
| `live` without explicit approval | reject/disable |
| `paper` with non-paper adapter | reject |
| Missing `MT5` terminal path when provider=`mt5` | reject |
| Secret in a normal config field | reject/redact |
| Unknown field | reject |

### A.7 Safety constraints

- Default `execution_mode=disabled`.
- Research/backtest modes cannot instantiate a broker adapter.
- Live mode is not selectable by a strategy, notification, or runtime signal.
- Config never silently changes strategy thresholds.
- A displayed effective config always redacts secrets.

### A.8 Tests

- Valid/invalid schema, unknown fields, duplicate JSON keys, non-finite numbers.
- XAUUSD-P profile accepted; GC/MGC legacy profile isolated.
- Mode/adaptor matrix; live authorization absent; secret redaction.
- Configuration fingerprint determinism and version mismatch.

### A.9 Completion criteria

Typed `XAUUSD-P` configuration exists; effective config is visible; execution is
disabled by default; existing strategy parameters are read from the authoritative
strategy module rather than duplicated.

---

## B. Market-Data Subsystem

### B.1 Purpose

Provide read-only, validated, point-in-time market data from MT5 or immutable
historical datasets. Distinguish complete/incomplete bars, fresh/stale data, and
provider failure.

### B.2 Inputs

- `MarketDataRequest` (existing) with instrument/timeframe/interval.
- MT5 read-only provider or historical file provider.
- Clock/evaluation timestamp supplied by the caller.
- Instrument profile and completion rules.

### B.3 Outputs

- `MarketDataResult` (existing) with explicit status and issues.
- Immutable `MarketObservation` sequences.
- `BarCompletion` metadata: open time, close time, complete flag.
- Dataset identity/checksum for historical runs.

### B.4 Dependencies

- `market_data/contracts.py`, `service.py`, `providers.py`, `mt5_provider.py`.
- Domain models; no execution imports.

### B.5 Persistent state

- MT5 connection/cache state in memory only.
- Historical dataset paths/checksums in run manifest.
- No source data is rewritten.

### B.6 Failure modes

| Failure | Behavior |
|---|---|
| Provider unavailable/timeout | explicit `PROVIDER_ERROR`/timeout |
| Empty response | `EMPTY`, never “no opportunity” |
| Invalid OHLC/type/symbol/timeframe | `INVALID`; no observations exposed |
| Future timestamp | reject |
| Duplicate/unordered timestamps | reject |
| Stale when max age supplied | `STALE`; no “current” claim |
| Incomplete latest bar | excluded/flagged; no decision |
| Missing bar in trigger window | strategy rejection |
| Reconnect storm | bounded retry/backoff; fail closed after budget |

### B.7 Safety constraints

- Read-only provider only in research/backtest.
- No account/order/position methods in market-data provider.
- No synthetic bars or gap filling.
- Runtime must not treat an open timestamp as a completed bar.
- Absolute UTC provenance and broker timezone remain labels, not verified facts.

### B.8 Tests

- Existing contract/service tests.
- Completion/boundary tests; incomplete current bar exclusion.
- Reconnect/timeout/fault injection.
- Dataset source identity and read-only checks.
- Freshness and stale result behavior.
- Runtime prefix equals historical view for the same data.

### B.9 Completion criteria

Same strategy inputs can be produced from historical files and live MT5 with
explicit completion semantics; failures are explicit; no order/account methods.

---

## C. Feature and Indicator Subsystem

### C.1 Purpose

Provide deterministic, completed-bar feature inputs for D1 bias, H4 structure,
M15 triggers, and confluence. Avoid duplicate strategy calculations.

### C.2 Inputs

- Validated D1/H4/M15 prefixes.
- Strategy parameters from `XAUUSDPMTFParameters`.
- Decision timestamp and next-M15 bar.
- Session/news snapshots when applicable.

### C.3 Outputs

- A feature snapshot or the equivalent internal values consumed by the strategy:
  EMAs, tolerance, previous high/low, ATR, confirmed pivots, S/R zones,
  Fibonacci zones, RSI, patterns, volume baseline, contiguity evidence.
- Feature provenance and timestamp availability.

### C.4 Dependencies

- `strategy/xauusd_p.py` private feature functions for the approved strategy.
- No separate production feature engine initially.

### C.5 Persistent state

- Historical prefix state is in memory per evaluation.
- Persistent decision record preserves zone/score/context needed for audit.
- No mutable global indicator cache across runs.

### C.6 Failure modes

- Insufficient warm-up/stale data -> strategy rejection.
- Missing/invalid RSI/volume/pattern -> withhold point, not fabricate.
- Unconfirmed pivot -> unavailable.
- Contiguity failure -> reject.
- Future data -> never included.

### C.7 Safety constraints

- One implementation of approved rules: `xauusd_p.py`.
- Research and runtime must call the same strategy/feature path.
- No reimplementation in backtester or execution.
- No future-confirmed pivot/ATR/zone may affect an earlier decision.

### C.8 Tests

- Existing strategy indicator/pivot/zone tests.
- Point-in-time prefix tests.
- Future-aligned H4 pivot invariance.
- Backtest vs direct strategy decision equality.
- Deterministic feature/score output.

### C.9 Completion criteria

All strategy features have a single authoritative implementation; replay and
runtime supply identical completed-bar inputs; future-data invariance passes.

---

## D. Strategy Engine

### D.1 Purpose

Run the approved `XAUUSDPMultiTimeframeStrategy` or explicitly labelled
`XAUUSDPMTFResearchVariant`; emit complete `StrategyDecision` objects.

### D.2 Inputs

- Completed D1/H4/M15 prefixes (or full series filtered internally).
- `decision_time`, `next_m15_bar`.
- `ResearchRiskState` for research eligibility.
- News context (or explicit unavailable).
- `research_mode`, `news_unaware`, `session_calendar_verified`.
- Strategy parameters and variant identity.

### D.3 Outputs

- `StrategyDecision` with status, score, confidence, session/news/risk context,
  candidate plan, rejections and `variant_id`.
- `XAUUSDPSignalCandidate` with TP1/TP2/partials/breakeven/blocked states.
- Canonical research record via `serialize_strategy_decision`.
- Legacy `TradeSignal` only for display/compatibility; never authoritative for
  execution or full management.

### D.4 Dependencies

- Domain models and strategy module.
- Market data/feature inputs.
- No execution, safety, persistence, or monitoring imports.

### D.5 Persistent state

- Decision itself is immutable; persistent record in `decisions`/`research_records`.
- No mutation of a prior decision after later data.

### D.6 Failure modes

- Missing risk state -> rejected decision (`RISK_UNAVAILABLE`), no candidate.
- Invalid data -> rejected decision with reason.
- Unverified session/news -> research rejection or labelled unaware mode.
- Unsupported symbol -> construction error.
- Serializer failure -> no authoritative record; fail closed.

### D.7 Safety constraints

- Approved rules/thresholds unchanged.
- H4 direction mandatory; no countertrend path.
- Strategy cannot create `OrderRequest` or call broker.
- Research variant remains news-unaware and session-isolated.
- `TradeSignal` is never a substitute for complete decision.

### D.8 Tests

- Existing `tests/test_xauusd_p_strategy.py`.
- Variant identity/filter/news invariants.
- Canonical record round-trip and failure behavior.
- Direct strategy vs backtest equality.
- Rejection reason and score preservation.
- Future-data and next-bar timing tests.

### D.9 Completion criteria

One strategy source; complete decision output; canonical integration; no
execution side effects; tests pass in research and paper modes.

---

## E. Risk-Management Subsystem

### E.1 Purpose

Keep research R-risk and monetary account-risk separate, deterministic and
fail-closed.

### E.2 Research risk inputs

- Candidate confidence and plan.
- Open hypothetical/paper positions.
- Entries today, consecutive losses, last exit/cooldown.
- Dataset-time day boundary.
- Duplicate decision/trade registry.

### E.3 Research risk outputs

- Eligible/ineligible research state.
- `RiskDecision` context for strategy/canonical record.
- Concurrency, daily entries, cooldown, duplicate rejection.
- Daily realized R, unrealized R, exposure, drawdown.
- `daily_loss_limit_status=not_evaluable_monetary`.

### E.4 Monetary account risk inputs (blocked)

- Verified account equity/start-of-day equity.
- Verified contract size/tick value/volume step.
- Verified risk per unit, margin, stop level.
- Account daily-loss state.
- Reconciliation state.

### E.5 Monetary outputs (blocked)

- `RiskAssessment` with approved quantity, risk amount, stop, notional.
- A separate `AccountRiskDecision`; never fabricated when inputs missing.

### E.6 Dependencies

- `strategy/xauusd_p.py` (existing R checks).
- `models.RiskAssessment`/`OrderRequest` for execution branch.
- Persistence for state; no execution below risk.

### E.7 Persistent state

- Daily entries, consecutive losses, last exit, cooldown, open positions.
- Daily R and account-risk state when verified.
- Position/trade IDs and duplicate registry.
- Account state must be reconciled at restart; unknown -> fail closed.

### E.8 Failure modes

- Missing loss/cooldown timestamps -> fail closed.
- Missing/inconsistent position state -> no new entries.
- Account risk unavailable -> no orders; research may still describe R.
- Daily loss state inaccessible -> no monetary claim.
- Duplicate candidate/entry -> reject.

### E.9 Safety constraints

- Strategy cannot bypass risk.
- No monetary sizing before economics verified.
- R results are not account returns.
- 3% account rule not claimed implemented.
- Account risk approval is mandatory for orders; research approval is not enough.

### E.10 Tests

- Existing strategy risk tests plus Phase E state tests.
- Concurrency, daily entry, cooldown, duplicate, stale state.
- Daily R/drawdown and open unrealized.
- Account-risk missing-input fail closed.
- Restart state recovery and reconciliation.

### E.11 Completion criteria

Research risk is complete and tested; monetary risk remains explicitly blocked;
no order path can bypass account risk.

---

## F. Trade-Execution Subsystem

### F.1 Purpose

Define and later implement the only path from an approved order to a broker, with
disabled-by-default behavior.

### F.2 Inputs

- Approved `RiskAssessment`/account risk decision.
- `OrderRequest` with stable `client_order_id`, symbol, side, quantity, type,
  price, stop/target references.
- `ExecutionMode` and broker capabilities.
- Instrument profile/volume constraints.

### F.3 Outputs

- `OrderState` snapshots; broker order ID; fill/cancel/reject state.
- Execution event log.
- Reconciliation records.
- No output from research/backtest path.

### F.4 Dependencies

- `execution/broker.py` protocol + `DisabledBroker`.
- Proposed paper adapter and MT5 demo adapter.
- `safety.OrderAuthorizer`/mode gate.
- Position manager/persistence.
- No strategy reverse dependency.

### F.5 Persistent state

- Order intents and idempotency keys.
- Broker acknowledgements/fills/cancellations.
- Submission attempts and timeouts.
- Unknown-outcome records requiring reconciliation.

### F.6 Failure modes

| Failure | Behavior |
|---|---|
| Execution disabled | `BrokerUnavailableError` |
| Missing mode authorization | reject |
| Invalid quantity/price/volume step | reject |
| Duplicate client ID | reject/do not resubmit |
| Timeout/unknown submission | mark unknown; reconcile before retry |
| Broker rejection | record; no fill assumption |
| Partial fill | reconcile; never mark filled |
| Stop/target placement failure | halt/manage position; incident |
| Account/position mismatch | fail closed; no new orders |

### F.7 Safety constraints

- Disabled by default; live requires separate explicit authorization.
- No research/backtest import; no order path from strategy.
- No retry with new ID while broker state unknown.
- Telegram/HTTP cannot confirm execution.
- No order during design stage.

### F.8 Tests

- Mock broker contract; disabled adapter raises.
- Mode matrix and authorizer provenance.
- Idempotency, timeout and unknown-state reconciliation.
- Partial fill/stop/target/restart.
- No execution imports in strategy/research.
- MT5 demo validation only after separate approval.

### F.9 Completion criteria

Paper adapter passes contract tests and reconciliation; demo adapter only after
explicit gate; live remains disabled/not implemented until final release.

---

## G. Position-Management Subsystem

### G.1 Purpose

Manage hypothetical, paper, and broker positions through the same lifecycle
semantics while keeping simulation and broker truth separate.

### G.2 Inputs

- Candidate plan: entry/reference, stop, TP1/TP2, fractions, breakeven.
- Completed M15 bars (research/paper) or broker updates (paper/live).
- Risk state and account state.
- Reconciliation snapshots.

### G.3 Outputs

- Position state, partial exit events, stop modifications.
- Realized/unrealized R (research) or P&L (paper/live only when verified).
- Open/truncated/reconciled status.
- Trade history and lifecycle audit.

### G.4 Dependencies

- `research/backtest/fills.py` for research semantics (reused conceptually).
- Proposed `positions/` manager.
- Broker/paper adapter; persistence.

### G.5 Persistent state

- Position ID, decision/trade IDs, side, size, entry, stop, targets,
  partial fractions, remaining fraction, state, exits, timestamps.
- Broker position ID and reconciliation status.
- Recovery checkpoint.

### G.6 Failure modes

- Missing stop/target/plan -> no entry.
- Fill outside plan -> flag/reject.
- Unknown broker state -> fail closed/no new entries.
- Stop modification rejected -> incident/manage manually.
- Restart with mismatch -> reconcile before action.
- Missing market data -> leave state unchanged; alert.

### G.7 Safety constraints

- Partial exits remain one position.
- No averaging down/pyramiding.
- Breakeven only after TP1 under approved rule.
- No invented broker fills.
- Research open/truncated positions never treated as broker positions.

### G.8 Tests

- TP1/TP2/partial/breakeven; gap/stop-first; open/truncated.
- Position-count and duplicate prevention.
- Paper/broker reconciliation; restart recovery.
- Manual intervention detection; mismatched state fail closed.
- Idempotent exit event processing.

### G.9 Completion criteria

Research lifecycle is deterministic; paper lifecycle reconciles; broker lifecycle
is only enabled after execution gate.

---

## H. News and Session Subsystem

### H.1 Purpose

Provide explicit, timestamped session/news evidence with fail-safe behavior.
Never convert missing data into “safe” or “verified”.

### H.2 Inputs

- Instrument profile (XAUUSD-P; legacy GC/MGC).
- Decision/entry timestamps in dataset time.
- As-of session/news snapshots with source, coverage, freshness, version.
- Research mode / production mode.

### H.3 Outputs

- `SessionEvaluation` with verified flag/filter mode/reason.
- `NewsEvaluation` with available/safe/point_awarded/unaware/reason.
- Explicit unavailable/stale/incomplete status.
- Research-only IANA overlap adapter; no COMEX mapping for XAUUSD-P.

### H.4 Dependencies

- Existing strategy `_session_evaluation`, `_news_evaluation`.
- Existing `market_data/gold.py` only for legacy GC/MGC.
- Proposed `sessions/`, `news/` provider contracts.

### H.5 Persistent state

- Provider snapshots/version/coverage recorded in decision metadata.
- No fabricated historical event or holiday.
- No mutable session timezone offset.

### H.6 Failure modes

- No verified XAUUSD-P calendar -> research unverified; live/demo blocked.
- Missing/stale/incomplete news -> production fail closed; research unaware only.
- Future snapshot -> unavailable.
- Provider exception -> explicit failure, no “no news” inference.
- Unsupported instrument -> reject.

### H.7 Safety constraints

- No COMEX calendar applied to XAUUSD-P.
- No verified-session/news claim without evidence.
- News-unaware variant remains labelled and cannot earn news point.
- Daily boundary remains dataset-time until broker day verified.

### H.8 Tests

- Existing session/news strategy tests.
- Missing/stale/future provider behavior.
- IANA overlap/DST boundaries in research.
- No COMEX leakage into XAUUSD-P.
- Provider fault injection.
- News point and safety window.

### H.9 Completion criteria

Research mode is honest and deterministic; packet/demo/live compliance remains
blocked until provider evidence is accepted.

---

## I. Research and Backtesting Subsystem

### I.1 Purpose

Produce deterministic gross price/R historical evidence without broker access.

### I.2 Inputs

- Immutable D1/H4/M15 datasets + long D1 warm-up.
- Dataset manifests/checksums.
- Strategy/research variant and parameter set.
- Fill/risk/split configuration.
- Optional canonical record settings.

### I.3 Outputs

- `BacktestResult` with decisions, trades, entry attempts, reports, sources,
  config fingerprint.
- `summary.json`, `trades.json`, `entry_attempts.json`, `decisions.jsonl`,
  `manifest.json`.
- Split metrics and labels.
- No orders/account output.

### I.4 Dependencies

- `strategy/xauusd_p_research.py`, `research_record.py`, `research/backtest/`.
- No `execution` or `safety` imports.

### I.5 Persistent state

- No persistent runtime state required; outputs are immutable artifacts.
- Manifest records config/strategy/data fingerprints.
- Run outputs never overwrite source data.

### I.6 Failure modes

- Checksum/schema/overlap failure -> abort.
- Missing data/warm-up -> decision rejections, not silent skips.
- Invalid fill -> fail closed/flag.
- Open/truncated -> report separately.
- Full historical run not executed unless explicitly instructed.

### I.7 Safety constraints

- Gross price/R only; no costs unless separately verified.
- No holdout tuning.
- No monetary sizing.
- No broker/order imports.
- R and price points not account return.
- Dataset-time labels.
- 30-trade threshold descriptive only.

### I.8 Tests

- Existing `tests/test_research_backtest.py`.
- Data identity/tamper; completed bars; future invariance.
- Entry/fill/TP/partial/BE/gap/stop-first.
- Risk state, cross-period, daily R, splits.
- Determinism, report outputs, canonical integration.
- Full replay equality on a small fixture.

### I.9 Completion criteria

Focused tests pass; one small smoke run documented; full experiment separately
authorized; no execution imports.

---

## J. Persistence and Recovery Subsystem

### J.1 Purpose

Persist the smallest durable state needed for reproducible research and safe
paper/live operation.

### J.2 Inputs

- Immutable canonical decision records.
- Trade/position/risk/order/reconciliation events.
- Configuration/run manifests.
- Health events.

### J.3 Outputs

- Append-only decision/research records.
- Mutable current-state tables with version/updated timestamps.
- Recovery snapshots and reconciliation results.
- Queryable history for operations/reports.

### J.4 Dependencies

- SQLite standard library; filesystem; no external DB.
- Domain/research serializers.

### J.5 Persistent state

SQLite WAL, single-process owner:

- `schema_version`, `runs`, `decisions`, `trades`, `positions`, `risk_daily`,
  `orders`, `order_events`, `reconciliations`, `config_snapshots`, `health`.
- JSON blobs for canonical records/parameters; indexed columns for run ID,
  decision/trade/client IDs, symbol, timestamps, status, variant.

### J.6 Failure modes

- DB lock/corruption -> fail closed; no order submission.
- Schema mismatch -> migration/rejection; no guessing.
- Missing prior state -> fail closed for execution.
- Duplicate ID -> reject/make idempotent.
- Partial write -> transaction rollback.
- Artifact/source mismatch -> fail run.

### J.7 Safety constraints

- Research records immutable; corrections are new records.
- No secrets in DB/artifacts.
- Broker-confirmed state is authoritative over internal stores.
- A DB restoration is not a broker reconciliation by itself.
- No distributed DB/queue.

### J.8 Tests

- Schema/migration, transaction, duplicate ID, restart.
- Decision/record immutability.
- Position/risk state recovery.
- Broker reconciliation after restart.
- Secret/redaction and access failure.

### J.9 Completion criteria

Research can be reproduced from records/manifests; paper can restart without
duplicate orders; live recovery remains blocked until reconciliation evidence.

---

## K. Monitoring and Telegram

### K.1 Purpose

Inform the owner, not authorize trading.

### K.2 Inputs

- Read-only status snapshots: mode, strategy, decisions, rejections, positions,
  daily R, risk limits, data freshness, connection/health, errors, backtest
  summary.
- Notification configuration and secret token.
- Delivery events.

### K.3 Outputs

- Daily briefing, decision/rejection notices, paper/live status, errors, health,
  risk-limit events, backtest completion.
- Delivery success/failure records.

### K.4 Dependencies

- Operations interface/persistence; outbound HTTP; no broker import.

### K.5 Persistent state

- Notification queue/attempts, last delivery, deduplication ID, rate limit.
- Secrets only from environment/secret store; redacted logs.

### K.6 Failure modes

- Token missing/invalid -> notifications disabled; no trading impact.
- Network failure -> retry with backoff; alert locally.
- Duplicate message -> deduplicate by event ID.
- Telegram delivered -> never treated as broker confirmation.

### K.7 Safety constraints

- No inbound Telegram command can execute a trade in the initial design.
- Notifications must include research/paper/live mode and evidence labels.
- No secrets in messages/logs.
- Failure to notify never changes risk state.

### K.8 Tests

- Message formatting, redaction, deduplication, retry/backoff.
- Disabled mode; no execution side effect.
- Label correctness; no confirmation claim.
- Failure injection.

### K.9 Completion criteria

Read-only notifications are reliable and cannot authorize orders; separate
approval required before any command interface.

---

## L. Operational Interface

### L.1 Purpose

Provide owner-visible health, mode, freshness, strategy/decision status, open
positions, R/risk, reports, errors, and alerts.

### L.2 Inputs

- Read-only queries over persistence/operations status.
- Report artifacts.
- Health/connection state.

### L.3 Outputs

- CLI output/status JSON; optional read-only web view later.
- Explicit unavailable/unknown states.
- No order controls in the initial interface.

### L.4 Dependencies

- Persistence, operations, monitoring; not broker write path.

### L.5 Persistent state

- No new state; reads persisted status.
- Optional cached view with expiry.

### L.6 Failure modes

- DB unavailable -> status unavailable; no fabricated healthy state.
- Stale data -> explicit.
- Unknown mode/position state -> visible warning.
- No implicit order controls.

### L.7 Safety constraints

- Read-only default.
- Never display R as account currency.
- Never display open/truncated research positions as broker positions.
- Never claim live readiness.

### L.8 Tests

- Status contract, stale/unknown, secret redaction, mode labels.
- No execution import or side effect.
- Report/run lookup and consistency.

### L.9 Completion criteria

Owner can determine mode, health, data freshness, R, risk state and blockers from
one interface without ambiguity.

---

## M. Logging and Diagnostics

### M.1 Purpose

Make every decision, rejection, order, reconciliation and failure auditable.

### M.2 Inputs

- Structured events with event ID/correlation ID/run ID/decision/trade/order IDs.
- Mode, symbol, timestamps, reasons, exception class.
- Secret values for redaction only.

### M.3 Outputs

- Structured JSON logs (local files/stdout), optional retention rotation.
- Decision/rejection traces and run manifest references.
- Redacted errors/alerts.

### M.4 Dependencies

- Standard library logging; safety redaction.

### M.5 Persistent state

- Log files with retention; no secrets; correlation index optional.
- Canonical decision/run IDs link logs to records.

### M.6 Failure modes

- Log write failure -> application continues only if safety-critical state is
  persisted elsewhere; otherwise fail closed.
- Rotation/retention failure -> alert.
- Redaction failure -> suppress message, never leak secret.

### M.7 Safety constraints

- No account credentials/tokens in logs.
- No logging a submitted order as filled.
- No Telegram delivery as confirmation.
- Logs are not a substitute for durable state.

### M.8 Tests

- Redaction, correlation, exception classification, retention, no secret leakage.
- Reconciliation/order event logging semantics.

### M.9 Completion criteria

A failed/ambiguous decision or order can be reconstructed from logs + persistence
without exposing secrets.

---

## N. Testing and Release Management

### N.1 Purpose

Define gates that prevent unverified research or unsafe execution from being
called complete.

### N.2 Test levels

1. Unit tests for models, config, risk, fills, persistence.
2. Integration tests for data -> strategy -> decision -> record.
3. Historical replay/golden tests; future-data invariance.
4. Property/invariant tests for coordinates, R, fractions, state transitions.
5. Fault injection for providers, broker, DB, notifications, restarts.
6. Safety tests for mode/import/provenance barriers.
7. Paper-trading tests with mock/paper broker.
8. MT5 demo validation (separate authorization).
9. Restart/recovery/reconciliation tests.
10. Full historical experiment (separate authorization).

### N.3 Required gates

- **Design gate:** owner accepts this package and resolves blocking decisions.
- **Research gate:** all tests + dataset checks + one small smoke; full historical
  only by explicit instruction.
- **Paper gate:** paper adapter/reconciliation/recovery tests; no live adapter.
- **Demo gate:** MT5 demo evidence, monetary/volume/economics, session/news
  handling, explicit authorization.
- **Live gate:** demo evidence, risk/account controls, independent operational
  readiness; explicit owner authorization.

### N.4 Release evidence

- Test command results.
- Config/dataset/strategy fingerprints.
- Manifest and report hashes.
- Known limitations.
- Go/no-go record with owner authorization.

### N.5 Completion criteria

Each gate has a frozen configuration, test evidence, limitations and an explicit
authorization record. No gate is inferred from a filename or a prior phase.

---

## O. Legacy Scanners and COMEX Components

### O.1 Purpose

Preserve existing GC/MGC research and COMEX schedule components without allowing
them into the XAUUSD-P path.

### O.2 Contract

- `scanners/` and `market_data/gold.py` remain under a legacy GC/MGC scope.
- They may not be used to approve XAUUSD-P instruments.
- No `XAUUSD-P` -> COMEX session mapping.
- Existing tests remain regression evidence; new XAUUSD-P work must not weaken
  them.
- A future owner decision may retire or re-scope them; that is not part of this
  design.

### O.3 Tests/completion

Existing GC/MGC tests remain green. XAUUSD-P integration tests prove no leakage
of COMEX asset class/symbol/calendar into the strategy path.
