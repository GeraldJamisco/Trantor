# Trantor

> **Current implementation note (2026-11-02):** The active strategy and research
> path use the broker instrument **`XAUUSD-P`** on the Fortune Prime Limited MT5
> environment. The GC/MGC/COMEX text below describes legacy Phase 1–3 components
> and is retained for historical compatibility; it is not the active instrument
> scope. See `docs/architecture/README.md` for the complete design and
> `docs/strategy/xauusd_p_strategy_spec_v1.md` for strategy rules.

Trantor is a Python research and trading-system foundation for **COMEX gold
futures**. The active target is **GC**, with **MGC** as the secondary supported
contract. The earlier multi-market scope has been retired; this README describes
the current gold-futures system only.

## Research dashboard

The repository includes a local, read-only browser dashboard for the completed
authoritative XAUUSD-P Phase E experiment. It reads the actual artifacts under
`research_results/xauusd_p/phase_e_authoritative_20261102/` and does not start a
backtest, connect to a broker, or submit orders.

Start it from PowerShell at the repository root:

```powershell
$env:PYTHONPATH='src'
python -m trantor.dashboard
```

Open:

```text
http://127.0.0.1:8765/
```

Use `Ctrl+C` in the terminal to stop it. Optional arguments:

```text
python -m trantor.dashboard --host 127.0.0.1 --port 8765
python -m trantor.dashboard --artifacts research_results/xauusd_p/phase_e_authoritative_20261102
```

The dashboard uses only the Python standard library and packaged static
HTML/CSS/JS. The decision history is indexed and paginated on the server; all
66,395 decision records are never sent to the browser at once.

Implemented views: Dashboard, Research & Backtest, Strategy, Market Chart, Trade
Explorer, Decision History, Data & Datasets, Reports & Results, System & Safety,
and Configuration. The UI is read-only. It does not include order controls,
broker settings, account data, live prices, or execution controls.

Intentionally deferred from this first version: live market data, account
balances, order lifecycle, demo/live controls, strategy optimization, trade-level
diagnostic analysis, multi-user authentication, and persistent UI state.

The repository currently contains **Phases 1 through 3**, the XAUUSD-P research
engine and completed Phase E artifacts, and a read-only local research dashboard.
It is not yet a strategy-evaluation engine, trading loop, broker client,
execution engine, scheduler, or notification system.

No strategy in this repository is validated. The shipped rule file is an
explicit placeholder with execution disabled, and the safety layer rejects it
for trading. Research candidates are not trade signals or orders. Market-data
tests and synthetic replay fixtures do not establish profitability.

## Active scope

- Supported asset class: **`gold_futures`** only.
- Supported target symbols: **`GC`** and **`MGC`** only.
- GC is the primary contract; MGC is the secondary contract.
- Supported market session: COMEX gold futures electronic trading, using a
  verified 2026-2027 schedule with explicit holiday and modified-session
  overrides.
- Paper mode is the only permitted mode. Live trading remains disabled.
- No stock, gold ETF, gold spot/CFD, foreign-exchange, crypto, or other
  instrument is an active target.
- No broker connection or order submission exists.

The strategy configuration loader rejects any other asset class or target
symbol, including otherwise recognized domain values. Unsupported instrument
types are rejected by the active gold configuration before analysis.

## What Phase 1 includes

- Typed domain models for instruments, observations, signals, risk assessments,
  order requests, order lifecycle states, positions, and trade records.
- Strict JSON configuration loading for gold strategy files and machine
  settings.
- A central fail-closed safety gate for paper-only execution.
- An order authorizer that requires an approved risk assessment and a matching
  `OrderRequest`, and rejects duplicate client order IDs in-process.
- A broker protocol with a disabled default adapter. **No broker connection or
  order submission is implemented.**
- Tests that run without a broker account, market-data subscription, network
  access, notification credentials, or secrets.

## What Phase 2 adds

- A read-only, broker-independent market-data contract:
  `MarketDataRequest`, `MarketDataProvider`, `ProviderCapabilities`,
  `MarketDataService`, and `MarketDataResult`.
- Explicit retrieval outcomes: `ok`, `empty`, `stale`, `invalid`,
  `provider_error`, and `unsupported`.
- Observation validation, duplicate/order checks, future-timestamp checks,
  requested-interval checks, and optional freshness enforcement.
- A COMEX gold-futures session calendar with open, break, and closed states.
  Default hours are Sunday 18:00 through Friday 17:00 America/New_York with a
  17:00-18:00 maintenance break Monday through Thursday.
- A verified default COMEX schedule for 2026-2027 with explicit holiday,
  early-close, halt, and special-session overrides. Outside that range,
  evaluation raises `SessionCalendarRangeError`.
- Explicit unverified-schedule handling for custom schedules; callers that
  require verification still reject them.
- Deterministic, offline tests and fixtures for market-data validation and
  session boundaries.

## What Phase 3 adds

- `GoldOpportunityAnalyzer`, which consumes validated Phase 2
  `MarketDataResult` objects for GC and MGC.
- Session-state gating, reference/prior-close selection, price range, data-age,
  exclusion, required-field, and required-measurement filters.
- Measurements for latest price, reference age, optional reference volume, and
  optional prior-close values (`previous_close`, `change`, `change_percent`,
  `absolute_change_percent`).
- Structured research reports that distinguish candidates found, no candidates
  passed, empty data, invalid data, provider failure, unsupported requests, and
  mixed batch outcomes. Per-instrument evaluations remain available beside the
  batch summary.
- Configurable, transparent ranking over raw measurements. Ranking is a sort
  order, not a probability of profit, expected return, or trade authorization.
- A strict, separately validated `opportunity_discovery` configuration block in
  `rules/gold.json`.
- Deterministic, offline replay fixtures. Replay observations still pass through
  `MarketDataService` and its Phase 2 validation before reaching the analyzer.

Not implemented in the live-execution path: broker connection, order submission,
live trading, NinjaTrader integration, notifications, and the scheduled trading
loop. Strategy research and the Phase E historical backtester are implemented
separately; the dashboard is a read-only research view.

## Safety invariants

- Paper mode is the only permitted mode. Live mode, `live_trading_enabled: true`,
  and non-paper broker adapters are rejected.
- Hard ceilings are independent of configuration files: 1% risk per trade, 10
  concurrent positions, and 5% daily loss. Files may be stricter, never looser.
- A strategy must be `validated`, execution-enabled, have entry conditions, and
  require a stop-loss before the safety gate passes.
- A `TradeSignal` is not an `OrderRequest`. An order requires an approved
  `RiskAssessment`, matching signal/strategy/symbol/side/quantity/stop, and a
  reserved unique `client_order_id`.
- Submitted and acknowledged orders are not fills. Only `OrderStatus.FILLED`
  with the full requested quantity counts as filled.
- The disabled broker adapter raises on every operation. There is no code path
  that connects to a brokerage or sends a live order.
- No secrets are written to logs. `.env` and `config/settings.json` are ignored,
  examples contain blank placeholders, and a logging redaction filter is
  provided for future logging setup.
- Market-data failures and empty responses are never converted into trading
  evidence, and invalid/stale results never expose observations as current data.
- Phase 3 analysis consumes validated results and never calls providers directly.
  It creates no `TradeSignal`, no `OrderRequest`, and no execution authorization.
- A candidate is research-only even when its strategy is marked `validated`;
  downstream strategy and safety gates are still required.
- An unverified COMEX schedule is never silently treated as verified.

The duplicate-order registry is process-local. A future execution adapter must
additionally reconcile broker state by client order ID and persist idempotency
state before retrying an unknown submission.

## NinjaTrader: intended future platform, not implemented

NinjaTrader is the intended future execution and market-data platform for this
project. That is a direction, not an implemented integration.

No NinjaTrader API, connector, endpoint, credential, account, sandbox,
permission, data subscription, authentication flow, or order-routing path is
implemented or assumed by this repository. No source file imports or calls
NinjaTrader.

Before an integration is designed, the following must be verified directly
against current NinjaTrader documentation and the intended account:

1. What integration method is actually available for automated access, and what
   documented constraints does it impose?
2. Can NinjaTrader supply the required historical and current GC/MGC market data
   at the configured timeframes, including the data subscription and feed?
3. Can it provide a demo or simulated execution environment that matches the
   intended paper workflow without enabling live execution?
4. What permissions, market-data subscriptions, fees, and geographic
   eligibility requirements apply?
5. How are order acknowledgements, fills, cancellations, and position updates
   confirmed and reconciled, and what are their failure modes?

Until those questions are answered and the integration is separately designed
and reviewed, paper remains the only permitted mode and live trading remains
disabled.

## Development sequence

1. Configuration, domain models, safety, and tests (Phase 1, complete).
2. Market data, validation, and COMEX session gating (Phase 2, complete).
3. GC/MGC research analysis and deterministic replay (Phase 3, complete).
4. Strategy evaluation, risk sizing, and a paper execution adapter (not started).
5. MT5 read-only market-data integration (Gate 1, complete); execution remains
   disabled.
6. Scheduled cycles, position management, and notifications (not started).
   The read-only research dashboard is implemented separately.

TradingView is not a dependency.

## Layout

```text
rules/
  gold.json                       Placeholder GC/MGC strategy configuration
config/
  settings.example.json           Safe paper-mode template; copy to settings.json
src/trantor/
  config.py                       Strict JSON loaders and typed configuration
  models.py                       Typed domain models and order lifecycle states
  safety.py                       Execution gate, order authorizer, redaction
  dashboard/                      Read-only local browser research dashboard
  strategy/__init__.py            Signal-only strategy protocol
  market_data/
    contracts.py                  Requests, capabilities, results, freshness
    providers.py                  Provider protocol and failure types
    service.py                    Validation and explicit retrieval outcomes
    sessions.py                   Shared session states and protocol
    gold.py                       COMEX gold-futures session calendar
    mt5_provider.py               Read-only MT5 OHLC provider (unverified symbol)
  scanners/
    models.py                     Research outcomes, candidates, rejections
    gold.py                       GC/MGC research analysis
    replay.py                     Strict synthetic replay loader/provider
    _common.py                    Selection, validation, data-failure helpers
  execution/broker.py             Broker interface and disabled adapter
  monitoring/__init__.py          Reserved for alerts and summaries
tools/
  mt5_readonly_probe.py           Read-only MT5 connection/symbol probe
  mt5_acquire_xauusd_p.py         Chunked research-history acquisition/audit
tests/
  test_config.py                  Valid, missing, malformed, unsafe configuration
  test_safety.py                  Paper/live guards, provenance, fills, idempotency
  test_market_data.py             Validation, provider outcomes, freshness
  test_mt5_provider.py            Read-only MT5 conversion, failures, shutdown
  test_mt5_acquire.py             Chunking, retries, validation, persistence, checksums
  test_gold_sessions.py           COMEX weeks, breaks, DST, holidays, modified sessions, range
  test_discovery_config.py        Strict gold discovery configuration
  test_gold_analysis.py           GC/MGC analysis, filters, verification, ranking
  test_scanner_replay.py          Replay validation and deterministic runs
  phase3_helpers.py               Deterministic Phase 3 domain-object builders
  fixtures/phase3_gold_replay.json Synthetic GC/MGC replay fixture
```

`pyproject.toml` provides standard `src/` packaging and pytest import
configuration. `execution/broker.py` keeps future broker code behind its required
interface. `market_data/` has no broker, account, order, or strategy code.

## Setup

Requirements:

- Python 3.12 or newer. Phases 1 through 3 were verified with Python 3.13.14.
- `tzdata` for `zoneinfo` on Windows.
- `pytest` for the test suite.
- For the optional read-only MT5 provider: `MetaTrader5==5.0.6231` on Windows
  x86-64 and a running MT5 terminal. The test suite does not require either.

The suite has no equity-calendar dependency, live provider, broker SDK, pandas,
or network requirement.

Clean environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For ad-hoc imports without pytest's path configuration:

```powershell
python -m pip install -e .
```

Create local machine settings from the template. The real file is ignored by Git
and is intentionally absent from a fresh checkout:

```powershell
Copy-Item config/settings.example.json config/settings.json
```

If settings are missing, loading fails instead of inventing defaults. The
example is paper-only.

### Environment variables

Machine-local values come from the process environment:

- `TRANTOR_SETTINGS_PATH` — path to the settings file. Defaults to
  `config/settings.json`.
- `TRANTOR_LOG_LEVEL` — optional `DEBUG`, `INFO`, `WARNING`, `ERROR`, or
  `CRITICAL` override.

`.env.example` documents reserved values for later phases. `.env` is ignored by
Git. There is still no application entry point, so `.env` is not auto-loaded.
No NinjaTrader endpoint, credential, account, sandbox, or permission value is
configured.

## Research data acquisition

The read-only MT5 history tool supports D1, H4, and M15 for the exact
`XAUUSD-P` symbol:

```powershell
python tools\mt5_acquire_xauusd_p.py --timeframe 1d --start 2024-01-01 --include-costs
python tools\mt5_acquire_xauusd_p.py --timeframe 4h --start 2024-01-01 --include-costs
python tools\mt5_acquire_xauusd_p.py --timeframe 15m --start 2024-01-01 --include-costs
```

Each invocation writes an immutable run directory under
`research_data/xauusd_p/runs/<UTC timestamp>/` with:

- `candles.csv` — normalized UTC OHLCV; `volume` is MT5 tick volume.
- `spread_volume.csv` — optional raw MT5 spread, `real_volume`, and
  `tick_volume`, written with `--include-costs`.
- `gaps.csv` — explicit chronological gaps; gaps are not classified as
  weekends or holidays.
- `metadata.json` and `validation.json` — checksums, requested ranges,
  chunk results, failures, completed-candle cutoff, and validation findings.

Generated research data is ignored by Git. Existing runs are never silently
overwritten. Audit a saved dataset with:

```powershell
python tools/mt5_acquire_xauusd_p.py --audit research_data/xauusd_p/runs/<run>
python tools\mt5_acquire_xauusd_p.py --audit research_data\xauusd_p\runs\<run> --refetch
```

`tick_volume` is not exchange-traded volume. Spread and `real_volume` are
research fields, not verified commission, financing, or execution costs.

## Market-data contract

`MarketDataRequest` combines a `MarketInstrument` (symbol and asset class), a
timeframe, and optionally an aware historical start/end interval. Start and end
must be supplied together.

A `MarketDataProvider` declares:

- a stable provider/source name;
- supported asset classes, symbols, and timeframes; and
- whether historical intervals are supported.

`MarketDataService.get_observations(...)` checks capabilities before calling a
provider. It then validates every returned observation and returns one
`MarketDataResult` with an explicit status:

| Status | Meaning |
| --- | --- |
| `ok` | Non-empty, valid, non-stale observations. |
| `empty` | The provider was queried successfully and returned nothing. |
| `stale` | Otherwise valid observations whose newest timestamp exceeds `max_age`. |
| `invalid` | The response failed validation; no observations are exposed. |
| `provider_error` | Timeout, unavailability, adapter failure, or unusable response. |
| `unsupported` | Provider capabilities do not cover the request; it was not queried. |

Only `ok` and `stale` results carry observations. `invalid`, `provider_error`,
and `unsupported` deliberately expose none. An `empty` result, invalid response,
or provider error is **not** evidence that no trading opportunities exist. Those
messages are explicit in the result.

Validation includes:

- required fields and strict `MarketObservation` construction;
- timezone-aware timestamps;
- finite, positive OHLC prices and valid OHLC ordering;
- optional non-negative volume (absence is never converted to zero);
- matching symbol and timeframe;
- duplicate timestamps and out-of-order observations;
- future timestamps beyond a one-minute clock-skew tolerance;
- observations outside a requested historical interval; and
- staleness against a caller-supplied `max_age` policy.

Freshness is only decided when `max_age` is supplied. The result then records the
evaluated time, age of the newest observation, threshold, and staleness decision.
`is_stale=None` means no policy was supplied, not that the data was proven fresh.
No live provider adapter is implemented or configured.

## COMEX session handling

The strategy `sessions` block in `rules/gold.json` is a placeholder *strategy
trading window*; it is not the market-session source of truth. Phase 2 provides
`GoldSessionCalendar` and `comex_gold_futures_schedule()`.

The shipped default schedule is verified for **2026-01-01 through
2027-12-31** in `America/New_York`:

- Sunday 18:00 through Friday 17:00 ET;
- a 17:00-18:00 ET maintenance break Monday through Thursday (`break`);
- exchange holiday halts, full closures, early closes, and modified sessions
  represented by explicit `SessionOverride` intervals (`closed`); and
- special exchange sessions represented by explicit `open` overrides.

The calendar is offline at runtime. Its rules were taken from official CME
Group sources:

- [CME Group Holiday and Trading Hours](https://www.cmegroup.com/trading-hours.html);
- the CME `trading-hours-by-product` service for GC (product 437) and MGC
  (product 5224), queried for 2026 and 2027; and
- the [CME GC contract specifications](https://www.cmegroup.com/markets/metals/precious/gold_contract_specifications.html).

The service data was retrieved on 2026-10-22. CME can revise holiday hours
until roughly two weeks before each holiday, so re-verify dates near a published
revision before relying on them. The service reports U.S. Central Time; the
schedule converts every boundary to
Eastern Time through IANA timezone data, never a fixed UTC offset. A `preopen`
event without a preceding `closed` event is treated as the start of the
exchange halt. The implemented modifications are:

- Monday holidays (Martin Luther King Jr. Day, Presidents' Day, Memorial Day,
  Labor Day, and observed Independence Day) halt at 14:30 ET and reopen at
  18:00 ET;
- Good Friday closes from the prior Thursday 17:00 ET through Sunday 18:00 ET;
- Juneteenth and Independence Day early closes are at 13:00 ET, followed by
  CME's special Saturday sessions on 2026-06-20, 2026-07-04, and 2027-06-19
  from 06:00 to 18:00 ET;
- Thanksgiving halts at 14:30 ET and the following day closes at 14:45 ET;
- the 2026 Christmas early close is 13:45 ET, with multi-day Christmas and New
  Year closures represented explicitly;
- the CME service's extra 2026-02-25 event is a 14:31-14:45 ET `break`.

The special Saturday sessions above are present in the CME service schedule;
some third-party holiday summaries mention only the Friday early close and the
Sunday reopen. The calendar follows the official CME service.

The recurring 17:00-18:00 ET maintenance window is `break`; holiday halts and
early closes are `closed`. Outside the verified range, `status_at(...)` raises
`SessionCalendarRangeError` before returning any state; there is no guessed
fallback. `require_verified=True` still raises `UnverifiedScheduleError` for an
explicitly unverified custom schedule, while the shipped default passes.
Weekend closure, overnight boundaries, DST conversion, daily breaks, holiday
closures, early closes, special sessions, and range failures are tested.

A reviewed custom `GoldSessionSchedule` can be marked verified and passed to the
analyzer. Non-futures instruments are rejected by
`gold_session_calendar_for(...)`; there is no equity or spot/CFD fallback.

## Gold analysis

The analyzer is a pure consumer of already validated Phase 2 results:

```text
MarketDataService -> MarketDataResult -> GoldAnalysisInput
                  -> GoldOpportunityAnalyzer -> GoldAnalysisReport
```

The analyzer never imports a provider, retrieves live market data, connects to a
broker, creates a `TradeSignal`, creates an `OrderRequest`, or authorizes
execution. Provider failures, empty responses, invalid responses, and unsupported
requests remain explicit rather than being converted into candidates.

Only `GOLD_FUTURES` instruments are analyzed. The strategy's configured supported
symbols are enforced again by the analyzer, so an instrument outside GC/MGC is
marked unsupported even if it is passed directly. The shipped placeholder
strategy supports both GC and MGC.

Gold candidates retain:

- the reference observation and optional prior-close observation;
- raw measurements;
- evaluated filters;
- missing-information names;
- a pass rationale;
- session verification state; and
- a deterministic rank.

The analyzer records `latest_price`, `reference_age_seconds`, optional
`reference_volume`, and optional prior-close measurements (`previous_close`,
`change`, `change_percent`, `absolute_change_percent`). It does not define a
validated entry rule, volatility model, trend rule, momentum score, or
probability of profit. A candidate is always `research_only`, never
`trading_eligible`, and never execution authorized.

The shipped default schedule is verified for 2026-2027. If a caller supplies an
explicitly unverified custom schedule and the configured strategy requires a
verified session, the instrument is rejected with `session_unverified`. An
explicitly unverified schedule can only produce a candidate when verification is
not required, and that candidate retains `session_verified: false`. A date
outside the verified range is rejected as `session_error`, with the range
exception included in the rejection message.

## Discovery configuration

`rules/gold.json` contains a strict `opportunity_discovery` block:

```json
{
  "kind": "gold_analysis",
  "reference_timeframe": "15m",
  "previous_close_timeframe": "1d",
  "previous_close_is_daily": true,
  "max_data_age_seconds": 300,
  "min_price": 1.0,
  "max_price": 100000.0,
  "required_fields": ["close"],
  "required_measurements": ["latest_price"],
  "eligible_sessions": ["open"],
  "excluded_symbols": [],
  "require_verified_session": true,
  "ranking": {
    "measurements": [
      {"measurement": "absolute_change_percent", "direction": "descending"},
      {"measurement": "latest_price", "direction": "descending"}
    ]
  }
}
```

The configuration loader rejects unknown fields, invalid ranges, duplicate list
values, mismatched timeframes, non-positive ages, unsupported measurements,
unsupported sessions, non-futures asset classes, and target symbols other than
GC/MGC. There is no permissive fallback.

`required_fields` must always include `close`. Supported ranking measurements are
`latest_price`, `previous_close`, `change`, `change_percent`,
`absolute_change_percent`, and `reference_volume`. Ranking uses the configured
criteria in order and then a deterministic symbol tie-break. A missing ranking
measurement sorts last; it is recorded as missing, not replaced with zero.

## Outcome semantics

Each report has a batch status and one evaluation per input. The batch status is
derived from the evaluations:

| Batch status | Meaning |
| --- | --- |
| `candidates_found` | At least one research candidate passed. Other instruments may still carry failures or rejections. |
| `no_candidates_passed` | Evaluations completed and all were rejected by configured rules or session eligibility. |
| `empty_data` | The only evaluation outcome was a successful but empty data response. |
| `invalid_data` | The only evaluation outcome was invalid data. |
| `provider_error` | The only evaluation outcome was a provider failure. |
| `unsupported` | The only evaluation outcome was an unsupported request or instrument path. |
| `mixed` | No candidate passed and the evaluations contain more than one non-candidate outcome. |

Per-instrument outcomes are `candidate`, `rejected`, `empty_data`,
`invalid_data`, `provider_error`, or `unsupported`. A candidate object has
`execution_authorized == False`, `is_trade_signal == False`, `is_order == False`,
and `trading_eligible == False`. No batch status is a probability, validation
claim, or execution permission.

## Replay fixtures

`tests/fixtures/phase3_gold_replay.json` is a synthetic, labelled GC/MGC fixture.
The replay loader rejects missing/unknown fields, invalid metadata,
non-synthetic disclaimers, unsupported kinds, unsupported asset classes or
symbols, malformed bars, and mismatched prior-close fields. Replay rows are fed
through `ReplayProvider` and `MarketDataService`, so fixture tests exercise the
Phase 2 validation path rather than bypassing it.

The replay tests use both a synthetic *verified* COMEX schedule and the shipped
verified default so the full analysis path can be exercised deterministically.
They also retain an explicit unverified-schedule rejection case. The fixture
demonstrates the shape of the contract only; it is not historical market data,
evidence of a profitable trade, or a gold trading recommendation.

## Tests

```powershell
python -m pytest
```

The suite requires no broker account, internet connection, market-data
subscription, notification credentials, MT5 terminal, or NinjaTrader
installation. MT5 provider tests inject a fake MT5 module; integration with a
real terminal is a separate, explicit check.

Coverage includes:

- GC/MGC acceptance and target-symbol enforcement;
- rejection of non-futures asset classes and unsupported symbols;
- paper-only safety and non-paper broker rejection;
- the disabled broker adapter;
- market-data validation, empty/stale/invalid/provider-error outcomes, and
  unsupported capabilities;
- read-only MT5 candle conversion, UTC normalization, missing symbols,
  initialization and connection failures, duplicate/unsorted/invalid candles,
  stale data, shutdown, and exact-symbol enforcement;
- COMEX weekends, daily breaks, DST, verified holiday and modified sessions,
  range failures, unverified-schedule safety, and non-futures instrument
  rejection;
- gold analysis filters, previous-close measurements, ranking, and research-only
  properties; and
- strict gold-futures replay loading and deterministic execution.

At this revision the complete suite reports **349 passed**. Targeted Phase 3
tests can be run directly:

```powershell
python -m pytest tests/test_discovery_config.py tests/test_gold_analysis.py tests/test_scanner_replay.py
```

These tests do not demonstrate profitability or validate a trading strategy.

## Next implementation stage

Phase 4 — strategy evaluation, risk sizing, and a paper execution adapter — has
not been started. Phase 3 deliberately stops at research candidates and
measurements. Any later phase must preserve the Phase 1 safety gates, consume
these results through an explicit strategy contract, and continue to avoid live
orders, broker connections, automated execution, Telegram, dashboards, and
scheduled trading loops until they are separately designed and reviewed.
