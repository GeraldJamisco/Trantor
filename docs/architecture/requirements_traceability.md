# Requirements Traceability Matrix

Legend:

- **EXISTING** — present and tested enough for current scope.
- **PARTIAL** — present but conflicts/shortfalls documented.
- **GAP** — not implemented.
- **BLOCKED** — cannot be completed without external evidence/authorization.

The strategy specification remains authoritative for rule details; this matrix
maps requirements to system owners and gates, not to a second rule definition.

## 1. Instrument and strategy requirements

| ID | Requirement | Status | Component | Implementation package | Verification |
|---|---|---|---|---|---|
| STR-01 | Active instrument is `XAUUSD-P`; no GC/MGC substitution | **PARTIAL** | Strategy, config, market data | WP1 | XAUUSD-P config/instrument tests; no COMEX leakage; legacy tests unchanged |
| STR-02 | Preserve approved D1/H4/M15 rules and thresholds | **EXISTING** | Strategy/feature | WP1–WP2 | Existing strategy tests; frozen parameter fingerprint; no threshold diff |
| STR-03 | D1 EMA50/200 bias with neutral band | **EXISTING** | Strategy/feature | WP2 | Existing strategy tests |
| STR-04 | Confirmed H4 2/2 pivots and structural trend | **EXISTING** | Strategy/feature | WP2 | Existing pivot/trend tests; future-aligned pivot regression |
| STR-05 | S/R and Fibonacci zones; directional eligibility | **EXISTING** | Strategy/feature | WP2 | Existing zone/directional tests |
| STR-06 | M15 RSI, reversal candle, tick-volume confluence | **EXISTING** | Strategy/feature | WP2 | Existing trigger/pattern/volume tests |
| STR-07 | Eight-point score and confidence tiers | **EXISTING** | Strategy | WP2 | Existing score/confidence tests |
| STR-08 | Mandatory H4 direction; no countertrend path | **EXISTING** | Strategy | WP2 | Existing H4 neutral/opposite tests |
| STR-09 | Structural stop, TP1=2R, TP2=3R, obstacle/min TP2 | **EXISTING** | Strategy | WP2 | Existing plan tests; no rule change |
| STR-10 | TP1/TP2 fractions, breakeven after TP1 | **EXISTING** | Strategy/position/fills | WP3 | Strategy and backtest partial/breakeven tests |
| STR-11 | Daily entry, concurrency, cooldown controls | **EXISTING** | Strategy/risk | WP7 | Existing strategy risk tests; Phase E risk tests |
| STR-12 | News/session restrictions | **PARTIAL** | Strategy/news/session | WP2, WP9 | Existing strategy tests; providers missing; research variant labelled |
| STR-13 | Research variant explicit and news-unaware | **EXISTING** | Strategy | WP2 | `tests/test_xauusd_p_strategy.py`; canonical variant/filter tests |
| STR-14 | Strategy returns complete decision, not reduced signal | **PARTIAL** | Strategy/interfaces | WP1–WP2 | Protocol correction; backtester consumes `StrategyDecision`; `TradeSignal` limitation test |

## 2. Data and provenance requirements

| ID | Requirement | Status | Component | Package | Verification |
|---|---|---|---|---|---|
| DATA-01 | Load immutable D1/H4/M15 XAUUSD-P datasets | **EXISTING** | Research data loader | WP3 | Checkums/row/schema tests; actual loader smoke |
| DATA-02 | Verify checksums, row counts, source identity | **EXISTING** | Data loader/backtest | WP3 | Dataset identity/tamper tests; manifests |
| DATA-03 | Use long D1 for warm-up only; reconcile overlap | **EXISTING** | Data loader | WP3 | D1 overlap reconciliation; 725 exact |
| DATA-04 | Completed bars only; no future data | **EXISTING** | Historical view/strategy | WP3 | View completion/future invariance tests |
| DATA-05 | Preserve gaps; no synthetic bars | **EXISTING** | Data loader | WP3 | Source validation; no fill logic in loader |
| DATA-06 | Dataset-time coordinate; no absolute UTC claim | **PARTIAL** | Strategy/records/reports | WP2–WP4 | Labels/limitations; provenance unverified |
| DATA-07 | Verified XAUUSD-P session/holiday calendar | **BLOCKED** | Session provider | WP9 | External evidence required |
| DATA-08 | Verified historical economic calendar | **BLOCKED** | News provider | WP9 | External evidence required |
| DATA-09 | No source/manifest mutation | **EXISTING** | Loader/backtest | WP3 | Read-only tests; git/source checks |

## 3. Research-record requirements

| ID | Requirement | Status | Component | Package | Verification |
|---|---|---|---|---|---|
| REC-01 | Canonical `trantor.research_decision` v1.0 | **EXISTING** | `research_record.py` | WP2 | Schema/round-trip tests |
| REC-02 | Preserve strategy/variant/filter identity | **EXISTING** | Record/strategy | WP2 | Identity/approved-vs-research tests |
| REC-03 | Preserve timestamp/dataset-time semantics | **EXISTING** | Record/backtest | WP2–WP3 | Time fields/limitations tests |
| REC-04 | Preserve score/confidence/session/news/plan/risk | **EXISTING** | Record | WP2 | Record completeness tests |
| REC-05 | Reject reduced candidate/TradeSignal as sole record | **EXISTING** | Record/backtest | WP2 | Serializer/backtester type tests |
| REC-06 | Reject non-finite/inconsistent/unsupported claims | **EXISTING** | Record validator | WP2 | Record integrity tests |
| REC-07 | Identity matching is structural, not provenance | **EXISTING/DOCUMENTED** | Record/identity docs | WP2 | Audit documentation; no provenance claim |
| REC-08 | Persist canonical decisions with run/trade IDs | **PARTIAL** | Record store/backtest | WP2, WP6 | Backtest decisions; durable store gap |

## 4. Backtesting and research requirements

| ID | Requirement | Status | Component | Package | Verification |
|---|---|---|---|---|---|
| BT-01 | Chronological completed-bar replay | **EXISTING** | Historical view/backtest | WP3 | Focused replay/view tests |
| BT-02 | No look-ahead; future-data invariance | **EXISTING** | Historical view/strategy | WP3 | Future append/prefix tests; aligned pivot tests |
| BT-03 | Next-M15-open entry reference | **EXISTING** | Fill engine | WP3 | Entry reference/invalid structure tests |
| BT-04 | Stop-first/gap/one-target rules | **EXISTING** | Fill engine | WP3 | Ambiguity/gap tests |
| BT-05 | TP1/TP2/partial/breakeven | **EXISTING** | Fill engine | WP3 | Long/short lifecycle tests |
| BT-06 | Daily realized R and drawdown | **EXISTING** | Backtest report | WP3 | Daily R tests |
| BT-07 | Open/truncated positions | **EXISTING** | Backtest report | WP3 | Cross-period/truncation tests |
| BT-08 | 2024/2025/2026 split separation | **EXISTING** | Backtest report | WP3 | Split tests/report schema |
| BT-09 | <30 trades inconclusive | **EXISTING** | Backtest report | WP3 | Split threshold tests/metadata |
| BT-10 | Gross-only R baseline | **EXISTING** | Backtest report | WP3 | Cost/label tests |
| BT-11 | Full historical experiment run | **NOT RUN** | Research execution | WP4 | Owner authorization; reports |
| BT-12 | No holdout tuning | **DESIGN CONTROL** | Research governance | WP4 | Frozen config/holdout run policy |

## 5. Risk and safety requirements

| ID | Requirement | Status | Component | Package | Verification |
|---|---|---|---|---|---|
| RSK-01 | Research R risk separate from monetary risk | **PARTIAL** | Strategy/backtest/risk | WP7 | R tests; account risk blocked |
| RSK-02 | Risk ceilings/stop validation/sizing blocks | **EXISTING/PARTIAL** | Strategy/risk | WP2, WP7 | Existing risk tests; monetary blocked |
| RSK-03 | Concurrency/daily entries/cooldown/duplicates | **EXISTING** | Strategy/backtest/risk | WP7 | Existing + backtest state tests |
| RSK-04 | Daily account 3% loss when economics verified | **BLOCKED** | Account risk | WP7/WP11 | Economics/equity unavailable |
| RSK-05 | Fail-closed stale/inconsistent state | **PARTIAL** | Strategy/risk/persistence | WP6–WP7 | Strategy tests; persistence gap |
| RSK-06 | No R-to-order shortcut | **DESIGN/EXISTING** | Risk/execution | WP7–WP10 | Interface separation and import scan |
| RSK-07 | Execution disabled by default | **PARTIAL** | Config/safety | WP1–WP10 | Existing paper/live guard; explicit mode pending |
| RSK-08 | No strategy direct broker access | **EXISTING** | Import boundaries | WP1–WP10 | Source/import tests |
| RSK-09 | No account/credential leakage | **EXISTING** | Safety/logging | WP12 | Redaction tests |

## 6. Execution and position requirements

| ID | Requirement | Status | Component | Package | Verification |
|---|---|---|---|---|---|
| EXE-01 | Separate market data/strategy/risk/order/broker layers | **PARTIAL** | Architecture | WP1–WP10 | Interface/import tests |
| EXE-02 | Order validation/volume/price/idempotency | **GAP** | Execution | WP10 | Contract/mock tests |
| EXE-03 | Broker rejection/timeout/partial fill handling | **GAP** | Execution | WP10 | Fault/mock tests |
| EXE-04 | Position reconciliation/restart recovery | **GAP** | Position/persistence | WP6–WP10 | Reconciliation/restart tests |
| EXE-05 | Paper adapter only; no live by default | **GAP** | Execution | WP8, WP10 | Paper tests; live disabled |
| EXE-06 | MT5 demo validation | **BLOCKED** | Execution | WP11 | External evidence + authorization |
| POS-01 | Open/TP1/TP2/partial/breakeven lifecycle | **EXISTING/PARTIAL** | Strategy/backtest | WP3, WP8 | Backtest lifecycle; runtime gap |
| POS-02 | Broker/internal state reconciliation | **GAP** | Position | WP6, WP10 | Restart/reconciliation tests |

## 7. Configuration/operations requirements

| ID | Requirement | Status | Component | Package | Verification |
|---|---|---|---|---|---|
| CFG-01 | Typed/versioned/validated config | **EXISTING/PARTIAL** | Config | WP1 | Existing config tests; XAUUSD-P profile gap |
| CFG-02 | Safe defaults/execution disabled | **PARTIAL** | Config/safety | WP1 | Existing paper-only; explicit mode pending |
| CFG-03 | Secret management/redaction | **EXISTING** | Safety/config | WP12 | Redaction tests |
| CFG-04 | Effective config visibility/fingerprint | **PARTIAL** | Config/backtest | WP1, WP3 | Backtest fingerprint; runtime visibility gap |
| OPS-01 | Health/mode/freshness/positions/R interface | **GAP** | Operations | WP12 | Status contract tests |
| OPS-02 | Structured logs/correlation/audit | **PARTIAL** | Logging | WP12 | Redaction/event tests |
| OPS-03 | Telegram/alerts, no execution confirmation | **GAP** | Monitoring | WP12 | Notification tests |
| OPS-04 | Persistence history/restart recovery | **GAP** | Persistence | WP6 | Schema/recovery tests |

## 8. Verification and release requirements

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| TEST-01 | Unit/integration tests | **EXISTING** | 450 passed at design time |
| TEST-02 | Historical replay/fill tests | **EXISTING** | 13 focused Phase E tests |
| TEST-03 | Future invariance/chronology | **EXISTING** | Strategy and backtest tests |
| TEST-04 | Property/invariant tests | **PARTIAL** | Model validation; more risk/state properties needed |
| TEST-05 | Fault injection/restart | **GAP** | Pending WP5–WP12 |
| TEST-06 | Safety/no-execution scan | **EXISTING/PARTIAL** | No execution imports in research; runtime gates pending |
| TEST-07 | Paper/demo validation | **BLOCKED** | No paper/demo adapter |
| TEST-08 | Full historical acceptance | **NOT RUN** | WP4 owner instruction |
| TEST-09 | Compile/diff gates | **EXISTING** | compileall; diff check warning only |
| REL-01 | No profitability/live-readiness claim | **CONTROL** | Documentation/review |
| REL-02 | Separate research/paper/demo/live authorization | **PARTIAL** | Existing paper-only safety; runtime mode pending |

## 9. Traceability gaps requiring design-owner decisions

1. `XAUUSD-P` config/profile migration (blocks integration; WP1).
2. `ResearchStrategy` protocol vs legacy `Strategy` (WP1).
3. Runtime execution mode enum/disabled default (WP1/WP10).
4. Session/news provider evidence and coverage (WP9; blocks demo/live).
5. Contract economics and monetary sizing (WP7/WP11; blocks demo/live).
6. Spread convention (cost-adjusted research; blocks cost-adjusted reports only).
7. Persistence/order reconciliation schema approval (WP6/WP10).
8. Paper model and broker evidence (WP8/WP11).
