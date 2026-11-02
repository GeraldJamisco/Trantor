# Trantor System Architecture

## 1. Purpose and scope

Trantor is a research-first, `XAUUSD-P` trading system for the Fortune Prime
Limited MetaTrader 5 environment. The architecture supports:

1. reproducible historical research;
2. paper-only strategy simulation;
3. later, separately authorized MT5 demo/live execution.

The system does not treat research evidence as execution authorization. It does
not assume that a filled backtest is a filled order or that Telegram delivery is
broker confirmation.

**Design-stage boundary:** this document specifies components and interfaces. It
does not implement them or run the experiment.

## 2. Current repository state

### Implemented and usable

| Area | Current implementation | Assessment |
|---|---|---|
| Domain models | `src/trantor/models.py` | Validated immutable models; `TradeSignal` is not a complete trade-management object |
| Configuration | `src/trantor/config.py` | Strict JSON loaders; currently scoped to GC/MGC |
| Safety | `src/trantor/safety.py` | Paper-only gate, order provenance, duplicate-ID guard, secret redaction |
| Broker boundary | `src/trantor/execution/broker.py` | `Broker` protocol and fail-closed `DisabledBroker`; no submission adapter |
| Market-data contracts | `src/trantor/market_data/contracts.py`, `service.py` | Typed requests/results, validation, explicit failure outcomes |
| MT5 read-only provider | `src/trantor/market_data/mt5_provider.py` | Read-only OHLC provider for `XAUUSD-P`; no account reads, orders, or login |
| Legacy GC/MGC analysis | `src/trantor/scanners/`, `market_data/gold.py` | Research analysis and COMEX sessions for the retired GC/MGC scope |
| XAUUSD-P strategy | `src/trantor/strategy/xauusd_p.py` | Approved D1/H4/M15 strategy and research-only candidate |
| Research variant | `src/trantor/strategy/xauusd_p_research.py` | Explicit news-unaware, session-isolated variant |
| Canonical records | `src/trantor/strategy/research_record.py` | Versioned research record serializer/validator |
| Phase E backtester | `src/trantor/research/backtest/` | Deterministic gross R replay, fills, splits, reports; full run not executed |
| Monitoring | `src/trantor/monitoring/` | Empty placeholder |
| Persistence | none | No durable decisions, positions, orders, risk state, or recovery |
| Scheduling | none | No recurring cycle |
| Telegram | none | No notification implementation |
| Execution/demo/live | none | No paper broker, MT5 order adapter, or live client |

### Current-state conflicts that must be corrected before integration

1. **Instrument scope conflict.** The README, `config.py`, `market_data/gold.py`,
   and legacy scanners are GC/MGC/COMEX-centric. The approved strategy and MT5
   provider are `XAUUSD-P`/`UNVERIFIED_GOLD`. The current configuration loader
   rejects `XAUUSD-P`.
2. **Strategy protocol conflict.** `strategy/__init__.py` describes a
   single-series strategy returning `TradeSignal | None`. The actual strategy
   consumes D1/H4/M15 prefixes and returns `StrategyDecision`.
3. **Risk-model conflict.** `safety.py` uses monetary `RiskAssessment` quantity
   fields and paper/live mode semantics. The research engine uses non-monetary
   R risk. Both must remain separate rather than sharing one approval object.
4. **Session conflict.** `GoldSessionCalendar` verifies COMEX GC/MGC sessions only
   and rejects `UNVERIFIED_GOLD`. The `XAUUSD-P` strategy uses an unverified IANA
   London/New York overlap. A COMEX calendar must not be applied to `XAUUSD-P`.
5. **Documentation drift.** README and Phase C/D documents describe earlier
   phases or state that later components do not exist. They remain useful
   historical records but are not the current runtime map.
6. **Runtime bar completion.** `MarketObservation` has an open timestamp but no
   explicit completed/candle-close field. Historical loader and strategy derive
   close time; runtime must make completion explicit and fail closed.
7. **No persistence/recovery.** `ResearchRiskState` is caller-supplied; there is
   no position registry, decision store, broker reconciliation, or restart
   recovery.
8. **Full historical evidence is absent.** The backtester exists, but the full
   development/validation/holdout experiment has not been run.

## 3. Target system principles

1. **One source of truth per domain**
   - Strategy rules: `xauusd_p.py` only.
   - Detailed strategy output: `StrategyDecision`.
   - Research archive: `trantor.research_decision` v1.0.
   - Account/order truth: broker reconciliation, never a signal.
2. **Research before execution.** Historical research and paper simulation are
   first-class modes that cannot reach the broker.
3. **Fail closed.** Missing timestamp provenance, stale data, unknown state,
   missing calendar evidence, unapproved monetary sizing, or uncertain broker
   state disables the affected action.
4. **Explicit evidence labels.** Every decision/report states variant, dataset
   time, gross/cost status, and limitations.
5. **No duplicated strategy logic.** Backtest, paper, and live execution call the
   same strategy implementation with point-in-time inputs. No parallel rules.
6. **No circular dependencies.** Data -> features/strategy -> decision -> risk
   -> simulation/execution -> persistence/reporting. Execution never imports
   strategy internals; strategy never imports execution.
7. **Idempotency and reconciliation.** Every order/position/decision has a stable
   ID; restart never assumes a fresh state or a confirmed fill.

## 4. Logical component map

```text
                         ┌────────────────────────────┐
                         │ A. Configuration & Identity │
                         └─────────────┬──────────────┘
                                       │
┌──────────────────┐        ┌──────────▼───────────┐
│ B. Market Data   │───────▶│ C. Feature Snapshot  │
│ MT5 / history    │        │ EMA RSI ATR pivots    │
└────────┬─────────┘        │ zones candles volume  │
         │                  └──────────┬───────────┘
         │                             │
         │                  ┌──────────▼───────────┐
         │                  │ D. Strategy Engine    │
         │                  │ approved + research   │
         │                  └──────────┬───────────┘
         │                             │
         │                  ┌──────────▼───────────┐
         │                  │ StrategyDecision      │
         │                  └──────┬────────┬──────┘
         │                         │        │
         │             ┌───────────▼──┐  ┌──▼────────────────┐
         │             │ I. Research  │  │ E. Risk Approval  │
         │             │ Replay/Fills │  │ R or monetary     │
         │             └───────┬──────┘  └───────┬────────────┘
         │                     │                 │
         │             ┌───────▼──────┐   ┌──────▼────────────┐
         │             │ J. Records   │   │ F. Execution      │
         │             │ decisions    │   │ disabled/paper/live│
         │             └───────┬──────┘   └──────┬────────────┘
         │                     │                 │
         │             ┌───────▼──────┐   ┌──────▼────────────┐
         └────────────▶│ G. Positions │◀──│ Broker/Adapter     │
                       │/reconciliation│   └───────────────────┘
                       └───────┬──────┘
                               │
                 ┌─────────────▼─────────────┐
                 │ K/L/M. Monitoring, Ops,    │
                 │ Logging, Alerts            │
                 └─────────────────────────────┘
```

### Responsibilities

- **A Configuration:** typed runtime profile, instrument identity, execution
  mode, secrets, versioning, freeze/fingerprint, visibility.
- **B Market Data:** read-only MT5/historical sources, completion, freshness,
  validation, provenance, reconnection, runtime/live snapshots.
- **C Feature Snapshot:** completed-bar EMA/RSI/ATR/pivots/S-R/Fib/pattern/volume
  /session/news inputs. Owned by the strategy boundary; not a second rule engine.
- **D Strategy Engine:** approved strategy and research variant; confluence,
  candidates, rejections, decision timestamps, variant identity.
- **E Risk:** separate `ResearchRiskEngine` (R, concurrency, entries, cooldown)
  and `AccountRiskEngine` (monetary, blocked until verified). No mixed object.
- **F Execution:** disabled/paper/live adapter boundary, validation, idempotency,
  order submission/confirmation, broker rejection handling.
- **G Position Management:** hypothetical positions, paper positions, broker
  positions; TP1/TP2/partials/breakeven; stop modifications; reconciliation;
  restart recovery.
- **H News/Session:** provider interfaces, event classification, freshness,
  fail-safe behavior, unverified research mode.
- **I Research/Backtesting:** historical replay, fills, splits, daily R, metrics,
  deterministic reports, canonical integration.
- **J Persistence:** SQLite/file artifacts, decisions, positions, risk, orders,
  reconciliation, config, health; append-only audit.
- **K Monitoring/Telegram:** daily briefing, decisions, rejections, hypothetical
  trades, health, errors, risk limits; never execution confirmation.
- **L Operational Interface:** health/status/mode/freshness/positions/R/reports/
  alerts; read-only by default.
- **M Logging/Diagnostics:** structured logs, correlation IDs, traces, redaction,
  retention.
- **N Testing/Release:** unit/integration/replay/property/fault/safety/paper/demo/
  recovery/acceptance gates.

## 5. Runtime modes and trust boundaries

| Mode | Strategy | Research risk | Broker orders | Persistence | Claims |
|---|---|---|---|---|---|
| `research` | approved or research variant | R only | Never | run artifacts | dataset/gross labels only |
| `backtest` | research variant (initial) | R only | Never | deterministic files | no live claim |
| `paper` | approved or research variant | R + simulated account | Paper adapter only | SQLite + files | paper-only |
| `demo` | approved, after gates | R + monetary verification | MT5 demo adapter | SQLite + reconciliation | demo evidence only |
| `live` | approved, separately authorized | account risk | live adapter | durable/reconciled | not ready |

`paper`, `demo`, and `live` are not interchangeable with `backtest`. The default
is `research`/execution disabled. A mode change requires explicit configuration
and the safety gate; it cannot be triggered by a strategy decision or Telegram.

### Trust boundaries

1. **Market-data boundary:** a provider response is untrusted until
   `MarketDataService` validates identity, ordering, completion, and freshness.
2. **Strategy boundary:** a strategy returns a decision, never an order. It has no
   execution import.
3. **Risk boundary:** research R approval and account risk approval are distinct;
   only account risk can authorize an order.
4. **Execution boundary:** only an authorized `OrderRequest` reaches a broker
   adapter; a `TradeSignal` or `StrategyDecision` cannot.
5. **Broker boundary:** broker state is authoritative for orders/positions;
   internal state is a reconciled projection.
6. **Notification boundary:** Telegram is outbound information, never proof of
   broker state.

## 6. Data flows

### 6.1 Historical research flow

```text
Validated D1/H4/M15 CSV + long D1 warm-up
  -> DatasetIdentity (checksums, row counts, source paths)
  -> HistoricalView.as_of(decision_time)
  -> Strategy.evaluate(prefixes, next M15 open)
  -> StrategyDecision
       -> canonical research record
       -> rejected/decision store
       -> research fill simulator (if candidate)
  -> trades/open/truncated positions
  -> split/daily R metrics
  -> deterministic reports + manifest
```

### 6.2 Live/paper observation flow

```text
MT5 read-only / historical provider
  -> MarketDataService validation
  -> completed-bar view
  -> StrategyDecision
  -> research record
  -> research risk or account risk
  -> simulation or paper/live execution
  -> position manager
  -> persistence and reconciliation
  -> monitoring/ops
```

The research path stops before risk approval that can create orders. The
execution path is unreachable unless `runtime mode` and safety gates permit it.

## 7. Repository architecture: current and target

### Current repository

See `src/trantor/` and `tests/`. Key boundaries already exist:

```text
config.py
models.py
safety.py
strategy/
market_data/
execution/
research/backtest/
scanners/                  legacy GC/MGC research
monitoring/                placeholder
tools/                     acquisition/probe tools
```

### Target additions (design only)

```text
src/trantor/
  instruments/              XAUUSD-P profile and legacy GC/MGC isolation
  risk/
    research.py             R-only risk
    account.py              monetary risk (blocked until verified)
  positions/
    models.py               hypothetical/paper/broker position views
    manager.py              lifecycle and reconciliation
  news/
    contracts.py            provider interface and event model
    providers.py            fail-closed adapters
  sessions/
    contracts.py            session provider interface
    xauusd_research.py      IANA overlap, explicitly unverified
  persistence/
    schema.py               SQLite schema and migrations
    store.py                append-only records/state
  operations/
    status.py               read-only operational snapshot
    cli.py                  health/status/report commands
  monitoring/
    telegram.py             outbound notification adapter
  execution/
    paper.py                paper adapter
    mt5_demo.py             future demo adapter
```

`scanners/` remains a legacy GC/MGC research package and is not on the
XAUUSD-P trade path. Do not delete it without owner approval; isolate it and
document its scope.

### 7.1 Module ownership

| Current or target path | Owning component | Contract/authority |
|---|---|---|
| `config.py` | A Configuration | Typed settings; no strategy rules |
| `instruments/` (proposed) | A Configuration/identity | XAUUSD-P profile; legacy GC/MGC isolation |
| `market_data/contracts.py`, `service.py`, `providers.py` | B Market Data | Validation/failure semantics |
| `market_data/mt5_provider.py` | B Market Data | Read-only XAUUSD-P OHLC |
| `market_data/gold.py`, `scanners/` | O Legacy GC/MGC | COMEX research only; not XAUUSD-P |
| `strategy/xauusd_p.py` | C/D Strategy/features | Sole implementation of approved rules |
| `strategy/xauusd_p_research.py` | D Strategy | Explicit research variant |
| `strategy/research_record.py` | D/J Record | Canonical decision contract |
| `safety.py` | E/F Safety/execution | Mode/risk/order authorizer; no strategy rules |
| `execution/broker.py` | F Execution | Broker protocol/disabled adapter |
| `positions/` (proposed) | G Position | Lifecycle/reconciliation |
| `news/`, `sessions/` (proposed) | H News/Session | Provider contracts/evidence |
| `research/backtest/` | I Research | Historical replay/reporting |
| `persistence/` (proposed) | J Persistence | SQLite/state/recovery |
| `monitoring/` | K Monitoring | Outbound notifications |
| `operations/` (proposed) | L Operations | Read-only status/CLI |
| logging setup (proposed) | M Logging | Structured/redacted diagnostics |
| `tests/`, release scripts | N Testing | Gates/evidence |

No module may independently own a second copy of strategy thresholds, risk
limits, or execution rules.

## 8. Dependency graph

```text
Configuration
  -> Market Data / Session / News providers
  -> Feature/Strategy
  -> Decision records
  -> Research risk / Account risk
  -> Simulation / Execution
  -> Position manager
  -> Persistence
  -> Operations / Monitoring
  -> Release validation
```

Hard dependency rules:

- `strategy` must not import `execution`, `safety`, `monitoring`, or `persistence`.
- `research/backtest` must not import `execution` or `safety`.
- `execution` must not contain strategy thresholds; it consumes approved
  `OrderRequest`/risk output only.
- `monitoring` must not mutate positions or authorize orders.
- `persistence` must not make strategy/risk decisions.

## 9. Deployment topology

The current project is a single-process Windows Python application. The smallest
suitable architecture is:

- one application process with a strategy/research loop;
- SQLite WAL database for durable state;
- filesystem artifacts for immutable research reports/manifests;
- one read-only MT5 terminal connection for market data;
- optional demo execution adapter only after explicit authorization;
- Telegram outbound client as an optional notifier;
- local CLI/status interface.

No distributed queue, microservice fleet, container orchestration, or external
database is justified by current requirements.

## 10. Design-stage completion criteria

The architecture design is complete when:

1. every component has a contract, owner, state, failure behavior and test gate;
2. data, strategy, risk, execution and persistence boundaries have no cycle;
3. all approved strategy/rules are referenced rather than redefined;
4. the current-state contradictions have a smallest-correction plan;
5. historical, paper, demo and live gates are distinguishable;
6. unresolved external facts are listed with their blocking stage;
7. the owner accepts the design package before implementation.
