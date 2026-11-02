# Trantor State, Interfaces, and Persistence

## 1. Interface design rules

- Inputs that affect a decision are immutable snapshots.
- Every cross-component object has an explicit type, schema/version, and
  validation boundary.
- A strategy output is a `StrategyDecision`, never an order.
- A research approval is not an account-risk approval.
- A broker snapshot is authoritative for broker orders/positions.
- Every persisted event has a stable ID and correlation ID.
- Unknown state fails closed for any action that can create or modify an order.

## 2. Canonical interfaces

### 2.1 Market data

Existing contracts remain authoritative:

```text
MarketDataRequest
ProviderCapabilities
MarketDataProvider.fetch_observations()
MarketDataService.get_observations()
MarketDataResult
MarketObservation
```

Proposed completion sidecar:

```text
BarCompletion
  symbol
  timeframe
  observed_at              # bar open time, dataset coordinate
  close_time               # observed_at + timeframe
  is_complete              # only complete bars may feed decisions
  source
  source_version
```

Rules:

- `observed_at` is the bar open, matching current strategy behavior.
- `close_time` is explicit; runtime must not treat `observed_at` as close.
- A historical provider may omit `BarCompletion` only when the loader proves
  native grids and completed rows from immutable source metadata.
- `MarketDataResult` continues to expose no observations on invalid/error.
- No synthetic bars, no forward-filled gaps.

### 2.2 Research strategy

Proposed protocol, compatible with the existing strategy implementation:

```text
ResearchStrategy
  identity: StrategyIdentity
  parameters: XAUUSDPMTFParameters
  evaluate(
    d1: Sequence[MarketObservation],
    h4: Sequence[MarketObservation],
    m15: Sequence[MarketObservation],
    *,
    decision_time: datetime,
    next_m15_bar: MarketObservation | None,
    risk_state: ResearchRiskState | None,
    news_context: NewsContext | None,
    research_mode: bool,
    news_unaware: bool,
    session_calendar_verified: bool,
  ) -> StrategyDecision
```

The old `Strategy` protocol returning a single `TradeSignal | None` remains a
legacy interface only. It must not be used for the XAUUSD-P path.

### 2.3 Decision record

Existing types:

```text
StrategyDecision
XAUUSDPSignalCandidate
TradePlan
Rejection / RejectionCode
ResearchRiskState
```

Existing canonical serializer:

```text
serialize_strategy_decision(decision, identity) -> dict
validate_research_record(record) -> dict
canonical_json(record) -> str
parse_research_record(payload) -> dict
```

Contract:

- record schema `trantor.research_decision`, version `1.0`;
- `StrategyDecision` is authoritative;
- `TradeSignal` and candidate-only objects are insufficient;
- variant/filter identity and limitations required;
- non-finite/compliance claims fail closed per the record contract;
- identity matching is structural, not provenance.

### 2.4 Research risk tracker

The strategy already enforces its R-based controls from `ResearchRiskState`.
The surrounding system supplies and persists that state; it does not duplicate
the strategy's decision logic.

Proposed tracker interface:

```text
ResearchRiskTracker
  snapshot(at) -> ResearchRiskState
  on_decision(decision, at) -> record/rejection
  on_entry(trade, at) -> state update
  on_partial_exit(trade, event) -> state update
  on_full_exit(trade, event) -> state update
  daily_report(at) -> R/drawdown/exposure
```

State fields remain those in `ResearchRiskState` plus an explicit `state_date`
and source/version metadata outside the strategy object if needed. Missing
cooldown timing or inconsistent state -> fail closed.

### 2.5 Account-risk approval

Proposed, **not implemented or enabled**:

```text
AccountRiskApprover
  approve(
    decision: StrategyDecision,
    account_snapshot: AccountSnapshot,
    instrument_economics: VerifiedInstrumentEconomics,
    open_positions: Sequence[BrokerPosition],
    daily_state: AccountDailyState,
  ) -> AccountRiskDecision
```

`AccountRiskDecision` must carry approved quantity/risk/stop/notional or an
explicit rejection. It is the only risk object that may authorize an
`OrderRequest`. It must not be fabricated from research R.

### 2.6 Execution and broker

Existing:

```text
Broker
  submit_order(OrderRequest) -> OrderState
  get_order(client_order_id) -> OrderState | None
  cancel_order(client_order_id) -> OrderState
  get_positions() -> tuple[Position, ...]
DisabledBroker
```

Proposed adapters:

- `PaperBroker`: deterministic in-process fills using a declared paper model.
- `MT5DemoBroker`: separate adapter, not implemented or enabled until demo gate.
- `LiveBroker`: not designed as enabled code; requires final gate.

All adapters must accept only an authorized `OrderRequest` and return
`OrderState`; no adapter is imported by strategy/research.

### 2.7 Position manager

```text
PositionManager
  open_from_fill(decision, fill, mode) -> PositionRecord
  apply_exit(trade_id, ExitEvent) -> PositionRecord
  apply_broker_positions(positions) -> ReconciliationResult
  snapshot() -> tuple[PositionRecord, ...]
```

Research positions and broker positions are distinct variants of a position
record. A broker position never inherits a hypothetical fill or R as if it were
confirmed P&L.

### 2.8 Session and news providers

```text
SessionProvider
  evaluate(instrument, decision_time, entry_time, require_verified) -> SessionEvaluation

NewsProvider
  snapshot(at, instrument, require_complete) -> NewsEvaluation
```

Provider output must carry source, coverage, version, `as_of`, verification
state and failure reason. Missing evidence cannot become “safe”. The research
XAUUSD-P session adapter uses IANA London/New York overlap and is explicitly
unverified. The legacy COMEX calendar is only for GC/MGC.

### 2.9 Persistence, operation and notification

```text
ResearchRecordStore.append(record) -> RecordRef
TradeStore.append(trade) -> TradeRef
StateStore.load/update(...) -> VersionedState
OperationalStatus.read() -> StatusSnapshot
Notifier.send(event) -> DeliveryReceipt
```

No notification or status read may call a broker or change risk state.

## 3. State models

### 3.1 Instrument profile (proposed)

```text
InstrumentProfile
  symbol: str                       # XAUUSD-P
  asset_class: AssetClass            # UNVERIFIED_GOLD
  exchange: str                      # BROKER
  currency: str                      # USD
  timezone: str                      # UTC label, provenance unverified
  session_scope: str                 # xauusd_research_unverified
  product_identity_verified: bool    # false
  contract_economics_verified: bool  # false
  source: str
```

GC/MGC legacy profiles remain separate:
`symbol in {GC, MGC}`, `gold_futures`, `comex_futures`. They must not be mixed
with the XAUUSD-P profile.

### 3.2 Strategy decision

Existing immutable `StrategyDecision`:

```text
instrument
decision_time
status (candidate | rejected)
candidate | None
rejections
score
daily_bias
h4_trend
zone
news
session
risk
research_mode
news_unaware
execution_ready (false)
variant_id
```

State transition:

```text
decision_time + completed inputs
  -> CANDIDATE or REJECTED
  -> immutable canonical record
  -> research simulation / risk state
  -> optional account-risk approval (execution branch only)
```

No later data may rewrite an earlier decision/record.

### 3.3 Research record

Canonical mapping to `trantor.research_decision` v1.0:

```text
record_hash = sha256(canonical_json(record))
run_id      = deterministic config/data/fill fingerprint
decision key = (run_id, record_hash)
```

`record_hash` is an integrity identifier, not a signature/provenance proof.
The record includes limitations and variant identity.

### 3.4 Research risk state

Existing `ResearchRiskState`:

```text
equity, start_of_day_equity        # neutral unit in research; no account claim
open_positions
entries_today
consecutive_losses
last_exit_at
cooldown_until
daily_locked_out
processed_trigger_times
```

Design addition outside the strategy object:

```text
ResearchRiskSnapshot
  state: ResearchRiskState
  state_date
  source_run_id
  state_version
  mode: research|paper
  consistent: bool
```

Invariants:

- `open_positions` counts active positions, not partial exits.
- `entries_today` counts actual entries, not rejected candidates.
- Loss cooldown starts at exit time; stale/missing timing fails closed.
- `daily_locked_out` is never set by unverified monetary loss.
- Neutral 1.0 equity is not account equity and must be labelled so.

### 3.5 Hypothetical/paper/broker position

```text
PositionRecord
  position_id
  origin (research_hypothetical | paper | broker)
  decision_id / trade_id
  symbol, side
  opened_at, entry_price
  initial_stop, current_stop
  tp1, tp2
  tp1_fraction, tp2_fraction
  remaining_fraction
  status (pending|open|tp1_filled|partial|closed|stopped|breakeven_stopped|open_truncated|reconciled_inconsistent)
  realized_r (research)
  realized_pnl (paper/live only after economics)
  broker_position_id
  last_reconciled_at
  version
```

The same lifecycle semantics are reused, but:

- research `realized_r` is not broker P&L;
- paper `realized_pnl` uses only a declared paper model;
- live P&L requires verified contract economics and reconciliation.

### 3.6 Order and broker state

Existing `OrderRequest`/`OrderState` remain the order contract:

```text
OrderRequest
  client_order_id (idempotency key)
  signal_id / decision_id
  symbol, side, quantity
  order_type, time_in_force, prices
  created_at

OrderState
  client_order_id
  broker_order_id
  status
  requested_quantity, filled_quantity
  average_fill_price
  updated_at
```

Rules:

- SUBMITTED/ACKNOWLEDGED != FILLED.
- PARTIALLY_FILLED requires 0 < filled < requested.
- Unknown/timeout requires reconciliation before retry.
- `client_order_id` reserved by the order authorizer.
- Only the execution branch creates these objects.

### 3.7 Daily risk and reporting state

Research:

```text
DailyRState
  dataset_date
  realized_r
  peak_to_trough_drawdown_r
  exit_events
  open_unrealized_r
  open_exposure_bars
  daily_loss_limit_status = not_evaluable_monetary
```

Monetary (blocked):

```text
AccountDailyState
  equity, start_of_day_equity
  realized_pnl
  unrealized_pnl
  costs
  daily_loss_fraction
  verified: bool
```

No account state is synthesized from R.

## 4. Trade lifecycle

### 4.1 Research/hypothetical

```text
Completed M15 trigger
  -> decision at trigger close
  -> next M15 open entry reference
  -> invalid protective structure? reject entry attempt
  -> fill hypothesis
  -> TP1 / TP2 / partial / breakeven / stop / gap
  -> open or closed/truncated
  -> exit events and daily R
  -> split/overall report
```

Rules: stop-first on ambiguous bar, gap-stop at open, gap-target at target,
one target per bar, TP2 inactive until TP1, no invented intrabar paths.

### 4.2 Paper

```text
Decision -> research R eligibility -> paper account risk -> paper order
  -> paper fill/confirmation -> internal position -> paper management
  -> reconciliation -> persistence -> notifications
```

Paper broker state is authoritative for paper fills, not the strategy.

### 4.3 Live/demo (future, disabled)

```text
Decision -> account risk -> order authorization -> broker adapter
  -> acknowledgement -> partial/full fill -> position reconciliation
  -> management -> broker confirmation -> persistence -> notification
```

No path may skip account risk, order authorization, idempotency, or
reconciliation.

## 5. Persistence design

### 5.1 Technology choice

SQLite with WAL, standard library only, single application process. Filesystem
stores immutable research artifacts and run manifests. No external database,
message broker, distributed lock, or microservice is justified.

### 5.2 Logical schema

| Table | Purpose | Key |
|---|---|---|
| `schema_version` | Migration/version record | version |
| `runs` | Run identity/config/data fingerprints/status | `run_id` |
| `decisions` | Canonical decision records and summaries | `run_id`, `decision_hash` |
| `trades` | Research/paper/broker trade records | `trade_id`, `origin` |
| `positions` | Current/reconciled positions | `position_id`, `account` |
| `risk_daily` | Research/account daily state | `account`, `dataset_date` |
| `orders` | Order intent/current state | `client_order_id` |
| `order_events` | Append-only order lifecycle | event ID |
| `reconciliations` | Broker/paper reconciliation snapshots | reconciliation ID |
| `config_snapshots` | Redacted effective config/fingerprint | config hash |
| `health` | Operational health events | event ID |

Append-only facts: decisions, order events, reconciliation facts, research
records. Mutable projections: current position/risk/order status, updated with
version/optimistic concurrency.

### 5.3 Identifiers

- `run_id`: deterministic hash of strategy/config/data/fill fingerprints.
- `decision_hash`: SHA-256 of canonical decision JSON.
- `trade_id`: `{run_id}:{sequence}` or paper/broker trade ID.
- `position_id`: internal ID; broker position ID stored separately.
- `client_order_id`: reserved by authorizer; contains strategy/variant/decision
  correlation but not secrets.
- `correlation_id`: run/cycle/request lineage.

### 5.4 Recovery

At startup:

1. load and validate config/mode;
2. open/migrate SQLite in a transaction;
3. load current risk/position/order state;
4. reconcile paper/broker orders/positions;
5. if any state is unknown, mark inconsistent and disable new orders;
6. never resubmit an order with an unknown broker outcome;
7. record the recovery result and emit an alert.

Research recovery only needs deterministic data/config/record validation; it
does not need broker reconciliation.

### 5.5 Consistency rules

- Immutable research records are never overwritten.
- A correction creates a new record with a parent/supersedes link.
- Broker state overrides internal projections.
- A DB copy/restore is not a broker reconciliation.
- No secret in DB/artifacts.
- Source datasets/manifests remain read-only; outputs live elsewhere.

## 6. Failure semantics summary

| Condition | Research | Paper | Demo/live |
|---|---|---|---|
| Invalid/missing historical data | reject/abort run | n/a | n/a |
| Stale/empty/error live data | reject | no new entries; manage existing with caution | same, fail closed |
| Unknown timestamp provenance | labelled limitation | no absolute-UTC/session claim | blocked |
| Missing cooldown/position state | no entries; record | no new orders | no new orders |
| Missing contract economics | R only | blocked | blocked |
| Missing spread/cost verification | gross only | gross-only paper or blocked cost model | blocked for cost claims |
| Unknown broker ack/fill | n/a | reconcile | reconcile; no retry |
| DB failure | abort/repair | fail closed | fail closed |
| Notification failure | alert locally | no trading effect | no trading effect |
