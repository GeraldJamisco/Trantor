# Trantor Risk, Safety, and Configuration Architecture

## 1. Risk architecture

Trantor has two distinct risk domains. They must never be represented by the same
approval object.

### 1.1 Research R-risk

Purpose: deterministic historical/paper research controls without account money.

Inputs:

- strategy decision and candidate plan;
- open research/paper positions;
- entries today;
- consecutive losses;
- last exit/cooldown;
- duplicate decision/trade IDs;
- dataset-time day boundary.

Outputs:

- eligible/ineligible `RiskDecision` for the research path;
- concurrency, daily entry and cooldown enforcement;
- daily realized R, unrealized R, exposure, drawdown;
- explicit `daily_loss_limit_status=not_evaluable_monetary`.

Non-goals:

- account-equity return;
- monetary sizing;
- notional/margin;
- 3% monetary daily-loss compliance.

### 1.2 Account risk

Purpose: authorize actual/paper order quantity only after verified economics.

Required verified evidence before implementation can be enabled:

- contract size, tick size, tick value;
- value per price unit and account-currency conversion;
- volume minimum/maximum/step;
- margin/leverage;
- commission, financing, slippage;
- broker stop/freeze levels;
- account equity/start-of-day equity;
- broker trading-day boundary.

The account risk engine returns `AccountRiskDecision`; it does not use research R
as quantity and does not approve an order from a `TradeSignal` alone.

## 2. Risk-control priority

When multiple restrictions apply, evaluate in this order:

1. **Mode/authorization** — research/backtest/paper/live gate.
2. **Data/provenance** — symbol, timeframe, completion, freshness, source.
3. **Strategy validity** — approved strategy/version/variant, H4 direction,
   mandatory filters, score.
4. **Research/account eligibility** — entries, concurrency, cooldown, duplicate,
   daily loss.
5. **Plan validity** — positive risk, stop/target geometry, partial fractions,
   breakeven, blocked sizing.
6. **Order authorization** — approved account risk, quantity/price/stop match,
   idempotency.
7. **Broker reconciliation** — no unknown order/position state.
8. **Execution**.

A failure at any stage stops the path; later stages cannot override it. The
strategy never calls stages 6–8.

## 3. Safety invariants

1. `XAUUSD-P` only for the active system. GC/MGC remain isolated legacy research.
2. Research/backtest never imports `execution` or `safety` and cannot submit
   orders.
3. A strategy returns `StrategyDecision`; `TradeSignal` is not an order and is
   not complete for TP2/partial management.
4. No `OrderRequest` without an approved account risk decision and authorizer
   reservation.
5. `SUBMITTED`/`ACKNOWLEDGED` are never treated as fills; only a complete
   broker-confirmed fill is filled.
6. No duplicate client order ID; unknown submission outcome requires
   reconciliation before retry.
7. Missing/inconsistent cooldown, position, risk or broker state fails closed.
8. Monetary sizing and account daily-loss claims are impossible until verified
   economics are configured and tested.
9. Costs are gross-only until spread/commission/financing/slippage/margin rules
   are verified.
10. No news/session compliance claim without verified providers and coverage.
11. Telegram/browser/status output never confirms broker execution.
12. A backtest is not evidence of live profitability or execution readiness.

## 4. Execution barrier design

The only order path is:

```text
StrategyDecision (approved strategy, research/execution-eligible)
  -> AccountRiskApprover (verified economics only)
  -> OrderAuthorizer (mode + provenance + idempotency)
  -> ExecutionAdapter.submit_order()
  -> OrderState / broker confirmation
  -> PositionManager reconciliation
  -> Persistence/Monitoring
```

The research path:

```text
StrategyDecision -> ResearchRiskTracker -> FillSimulator -> ResearchRecord
```

There is no edge from `ResearchRiskTracker` to `OrderAuthorizer`.

### Mode gate

Proposed execution modes:

```text
disabled     # default; no adapter instantiated
research     # no broker; historical/live observation only
paper        # paper adapter only; no external orders
demo         # MT5 demo adapter, explicitly authorized
live         # live adapter, explicitly authorized, final gate
```

The existing `TradingMode.PAPER/LIVE` and `live_trading_enabled=false` safety
behavior remains a legacy/backward-compatible guard. The new runtime mode must be
stricter or equivalent; it cannot permit execution when the old guard would fail.

### Design-stage barrier

Even with implementation authorization for research, the design does not
authorize:

- enabling `PaperBroker` against a real account;
- creating an MT5 order adapter;
- changing account settings;
- placing/canceling an order;
- enabling live trading.

## 5. Configuration specification

### 5.1 Configuration layers

1. Repository defaults (safe, explicit, no secrets).
2. Environment-specific runtime config.
3. Environment variables for secrets only.
4. Explicit command-line operational options (no secret/strategy threshold
   overrides).
5. Frozen resolved config with fingerprint.

No invalid file falls back to defaults. Every loader returns a validated immutable
object or raises `ConfigurationError`.

### 5.2 Proposed `RuntimeSettings`

```text
schema_version
runtime_mode: disabled|research|paper|demo|live
execution_enabled: bool                 # must be false unless mode allows
instrument_profile: InstrumentProfile
market_data_provider: disabled|mt5|historical
mt5_terminal_path: string|null
strategy_profile: xauusd_p_approved|xauusd_p_research_isolated
strategy_parameters_ref: path/hash
research:
  initial_capital_unit: 1.0             # neutral research unit, not money
  max_daily_entries: 3
  max_concurrent_positions: 2
  cooldown_loss_bars: 16
  cooldown_two_loss_bars: 32
  daily_loss_limit_status: not_evaluable_monetary
paper:
  adapter: paper|disabled
  starting_unit_balance          # paper simulation only
persistence:
  sqlite_path
  artifact_root
monitoring:
  telegram_enabled
  telegram_chat_id
  telegram_token_env
logging:
  level
  retention_days
  redact_secrets
  paths
safety:
  live_requires_explicit_authorization
  mandatory_order_authorizer
  fail_closed_on_unknown_state
```

The exact field set is approved during implementation package A, but the
semantic constraints below are fixed.

### 5.3 Instrument profile

`XAUUSD-P`:

```text
symbol = XAUUSD-P
asset_class = UNVERIFIED_GOLD
exchange = BROKER
currency = USD
timezone = UTC label (absolute provenance unverified)
session_scope = xauusd_research_unverified
product_identity_verified = false
contract_economics_verified = false
```

Legacy `GC`/`MGC` remain separate `GOLD_FUTURES` COMEX profiles. The XAUUSD-P
configuration must not be routed through `GoldSessionCalendar`, which rejects
non-COMEX symbols.

### 5.4 Secrets

- Secrets are read only from environment/secret store, never regular JSON.
- Examples contain placeholders.
- `SecretRedactingFilter` is attached to every log handler.
- Errors never echo tokens/passwords/account credentials.
- Telegram token/chat IDs are not written to reports or SQLite.
- No secret is included in run fingerprints; only a hash/identifier of the
  non-secret config.

### 5.5 Configuration visibility

Every run/operation exposes a redacted effective configuration:

```text
config_fingerprint
runtime_mode
instrument_profile
strategy name/version/variant
execution_enabled
market_data provider
session/news evidence status
persistence/artifact roots
monitoring enabled/disabled
```

The owner can determine whether execution is disabled without reading source.

### 5.6 Environment separation

| Environment | Config source | Execution | Data | Persistence |
|---|---|---|---|---|
| research | repo/config | none | historical or read-only MT5 | artifacts |
| paper | paper config | paper adapter only | read-only MT5/historical | SQLite |
| demo | demo config + authorization | MT5 demo only | MT5 demo/full | SQLite + reconciliation |
| live | live config + separate authorization | not enabled by this design | MT5 live | SQLite + reconciliation |

Environment variables must not silently switch `research` to `live`.

## 6. Failure behavior by control

| Control | Failure behavior |
|---|---|
| Config invalid | abort before any data/strategy/broker initialization |
| Execution mode unknown | disable execution |
| Instrument mismatched | reject; no provider/strategy call |
| Data stale/incomplete | no decision/entry |
| Session calendar unavailable | research unverified only; demo/live blocked |
| News calendar unavailable | research unaware only; demo/live blocked |
| Strategy invalid | no candidate |
| Risk state missing/inconsistent | no entry; alert |
| Account economics missing | no order; research may continue |
| Spread unverified | gross only; no cost-adjusted result |
| Order authorizer rejects | no submission |
| Broker timeout/unknown | mark unknown; reconcile; no blind retry |
| Position mismatch | disable new orders; reconcile |
| DB failure | fail closed for order path; repair before restart |
| Notification failure | local alert; no trading impact |

## 7. Existing safety/configuration corrections (smallest)

1. Add a `XAUUSD-P` instrument/profile path without removing legacy GC/MGC; do
   not widen `GoldSessionCalendar` or COMEX rules to `UNVERIFIED_GOLD`.
2. Add a `ResearchStrategy` protocol; retain the old `Strategy` protocol as
   legacy.
3. Add an explicit execution mode/barrier defaulting to disabled; preserve the
   old paper/live safety guard.
4. Separate research R-risk from monetary `RiskAssessment`.
5. Add runtime completed-bar metadata; do not infer completion from open time.
6. Add session/news provider interfaces; do not claim compliance from missing
   providers.
7. Add persistence before paper/demo; no order without durable idempotency.
8. Update README/current docs after code correction (not in design stage).

## 8. Validation and release gates

| Gate | Required evidence |
|---|---|
| Design | owner acceptance of package, blocking decisions resolved |
| Research | focused/full tests, dataset checksums, small smoke, gross labels |
| Paper | paper adapter/risk/persistence/reconciliation/recovery tests |
| Demo | MT5 demo evidence, instrument economics, session/news, explicit owner authorization |
| Live | demo validation, account-risk controls, operational readiness, explicit owner authorization |

No gate may be satisfied by a strategy score, a backtest return, a Telegram
message, or a file name alone.
