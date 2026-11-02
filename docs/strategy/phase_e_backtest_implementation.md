# Phase E Research Backtester Implementation

- **Status:** WP2/WP3 integration hardening and the authoritative pre-run checkpoint are implemented; tests passing. The full historical experiment has not been run.
- **Scope:** `XAUUSD-P` only, research-only, gross price/R, dataset-time.
- **Implementation:** `src/trantor/research/backtest/`
- **Strategy:** `XAUUSDPMTFResearchVariant`, the explicitly labelled research-isolated variant of `XAUUSDPMultiTimeframeStrategy`.
- **Broker/execution:** None. The package does not import `trantor.execution` or `trantor.safety`, place orders, size positions, or calculate account currency.

---

## 1. Labels

Every report carries:

```text
RESEARCH-ONLY FILTER VARIANT - NOT COMPLETE APPROVED STRATEGY
GROSS BASELINE - EXECUTION COSTS EXCLUDED
```

The research variant isolates the unverified session and news filters. It does
not claim complete-strategy, session, news, or live-execution compliance.

---

## 2. Inputs

The loader reads the authoritative final Phase B runs and the long D1 warm-up:

| Role | Run directory |
|---|---|
| D1 final | `research_data/xauusd_p/runs/20261022T205633Z` |
| H4 final | `research_data/xauusd_p/runs/20261022T205633Z-01` |
| M15 final | `research_data/xauusd_p/runs/20261022T205651Z` |
| D1 warm-up | `research_data/xauusd_p/runs/20261022T194021Z` |

It verifies:

- dataset metadata symbol, timeframe, checksum, row count, first/last timestamps;
- CSV schema and strictly ordered unique timestamps;
- native D1/H4/M15 grids;
- OHLC validity through `MarketObservation`;
- exact D1 overlap reconciliation between the long warm-up and authoritative
  final D1;
- source paths and SHA-256 identities.

Source CSV files, metadata, validation files, and manifests are read-only.

`DatasetSpec` supports explicit paths and expected identities for tests and
future controlled datasets. The default identity is the Phase B final set above.

---

## 3. Replay and look-ahead protection

`HistoricalView.as_of(decision_time)` returns:

- D1 bars whose close is at or before the decision timestamp;
- H4 bars whose close is at or before the decision timestamp;
- the most recent 64 completed M15 bars ending at the decision timestamp;
- exactly the immediate next M15 bar for the entry reference.

The strategy receives the as-of D1/H4 prefixes and the bounded M15 window. The
strategy itself also filters by bar close. No future D1, H4, M15, pivot, ATR, or
zone data participates in an earlier decision. Appending future data does not
change an existing as-of view.

Decision timestamp:

```text
decision_time = close_time(M15_T)
entry_time    = open_time(M15_T+1)
entry_price   = open(M15_T+1)
```

The entry is never filled at the trigger close.

---

## 4. Fill and position model

The fill engine is a pure research simulator in
`src/trantor/research/backtest/fills.py`.

Rules implemented:

- initial structural stop from the candidate plan;
- TP1 and TP2 from the candidate plan;
- configured partial fractions (currently 50/50);
- TP2 inactive until TP1 fills;
- breakeven stop at entry after TP1;
- stop-first when a bar could touch both stop and target;
- gap-through-stop fills at the bar open when worse than the stop;
- gap-through-target fills at the target price, not a better gap price;
- at most one target event per bar;
- no intrabar tick path is invented;
- no entry if the next open is at/beyond the protective stop or TP1;
- no entry if the next open differs from the decision entry reference or the
  risk distance is inconsistent.

Open positions at the run end are marked `open_or_truncated`; no synthetic close
price is created. Positions still open at a split cutoff are excluded from that
split's closed-trade metrics.

OHLC cannot identify the exact intrabar sequence. The implementation records
ambiguous stop/target bars and follows the approved conservative stop-first
convention.

---

## 5. Risk and continuous state

The engine uses `ResearchRiskState` as a non-monetary research carrier:

- open strategy positions;
- entries today;
- consecutive losses;
- last exit and cooldown;
- duplicate decision timestamps (engine-level fail closed).

The strategy enforces its existing maximum concurrent positions, maximum daily
entries, and cooldown rules against this state. State is not reset at split
boundaries: carried positions count toward concurrency, and their later closure
is reported as cross-period/truncated for the entry split.

Monetary equity is not used. The internal neutral unit is not account equity.
The 3% account-equity daily-loss rule is reported as:

```text
daily_loss_limit_status = not_evaluable_monetary
```

Daily realized R and daily realized-R peak-to-trough drawdown are reported in R
only.

---

## 6. Splits

| Split | Decision window |
|---|---|
| Development | 2024-01-01 through 2024-12-31 23:59:59.999999 |
| Validation | 2025-01-01 through 2025-12-31 23:59:59.999999 |
| Holdout | 2026-01-01 through 2026-10-21 23:59:59.999999 |

Trades are attributed to the split containing the entry **decision** timestamp.
Primary split metrics include only trades entered in that split and fully closed
by that split's cutoff. Cross-period and open/truncated positions are reported
separately and are not counted as new entries in the later split.

Fewer than 30 closed trades in a split is labelled:

```text
inconclusive_fewer_than_30_closed_trades
```

Thirty trades is a preliminary reporting threshold, not statistical proof.

---

## 7. Outputs

`BacktestResult.write_reports(output_dir)` writes deterministic artifacts:

```text
summary.json
trades.json
entry_attempts.json
decisions.jsonl
manifest.json
```

`summary.json` contains labels, strategy identity/parameters, instrument
profile, source identities, split summaries, overall metrics, daily R/drawdown,
gross-only cost status, assumptions, data limitations, the canonical
research-record contract identity, and the configuration fingerprint.

`decisions.jsonl` contains canonical research-decision records for every
decision, including rejected decisions. Records use the versioned
`trantor.research_decision` contract and identify the research variant. Each
record carries a deterministic `record_hash` derived from its canonical JSON.

`trades.json` and `entry_attempts.json` carry the matching `decision_hash` for
each simulated trade/attempt, linking simulation data back to the authoritative
`StrategyDecision` record. The legacy TP1-only `TradeSignal` is not used as the
simulation source.

`manifest.json` contains the deterministic configuration fingerprint, strategy
identity and parameters, instrument profile, source checksums and identities,
assumptions, limitations, labels, split definitions, record-contract identity
(including authoritative status), and decision/trade linkage counts. No
wall-clock generation timestamp is included.

### Authoritative-run gate

`BacktestConfig.authoritative` defaults to `True` for the historical experiment.
An authoritative configuration must also keep `record_decisions=True`; otherwise
configuration loading fails closed. After replay, the engine validates that:

- every decision has a canonical record and a matching record hash;
- every trade has a non-empty `decision_hash` matching its decision record;
- every entry attempt links to its decision record;
- trade and attempt decision timestamps resolve to exactly one record;
- reported record/linkage counts match the in-memory records.

`write_reports(..., include_decisions=False)` is rejected for an authoritative
result. The decision JSONL cannot be omitted from authoritative evidence.

The output directory is new research output only. Source datasets and manifests
are never modified.

---

## 8. How to run

From the repository root:

```text
python -m trantor.research.backtest --output research_results/xauusd_p/phase_e
```

`--no-decisions` is diagnostic-only. It disables canonical decision recording,
marks the run non-authoritative, omits `decisions.jsonl`, and is not valid for
owner acceptance of the historical experiment.

The authoritative command (without `--no-decisions`) loads the default
authoritative datasets, verifies their checksums, replays the split windows,
validates decision/trade linkage, and writes reports. It does not place orders,
size positions, or contact a broker.

The full historical experiment is not run by the implementation tests. Run it
only under the owner's explicit execution instruction.

---

## 9. Tests

The focused test suite is:

```text
python -m pytest tests/test_research_backtest.py -q
18 passed
```

It covers dataset identity/tamper rejection, resource loading, D1 warm-up
reconciliation, point-in-time views, completed-bar timing, future-data
invariance, direct-strategy replay equality, entry invalidation, long/short gap
stops, stop-first ambiguity, TP1/TP2, partials, breakeven, risk-state
transitions, cross-period truncation, daily R/drawdown, canonical
decision-record/hash integration, manifest identity, deterministic replay,
split boundaries, missing-input rejection, authoritative linkage validation,
diagnostic-mode behavior, execution isolation, and deterministic report writing.

The full repository verification at checkpoint time:

```text
python -m pytest -q
469 passed

python -m compileall -q src tests
successful

git diff --check
only the pre-existing README.md LF/CRLF warning
```

A small actual-data smoke run evaluated 276 early development decisions through
2024-01-05 and produced no candidates; the full historical experiment was not
run.

The pre-run reproducibility checkpoint used a deterministic
2024-01-01 through 2024-03-31 window twice with the same datasets,
configuration, strategy, and settings. Both runs produced 5,776 canonical
decisions and 3 linked trades; decisions, trades, entry attempts, manifest, and
summary metrics were byte-identical across output files.

---

## 10. Known limitations

- Timestamps are `dataset_time`; absolute UTC provenance is unverified.
- The research variant isolates session/news filtering; session and news
  compliance are not established.
- The approved news point is unavailable; `news_unaware=true`.
- The gross baseline excludes spread, commission, financing, slippage, margin,
  and account-currency effects.
- Monetary sizing, account-equity returns, and the 3% daily-loss limit are not
  evaluable.
- Partial-exit volume feasibility is blocked.
- M15 OHLC cannot establish intrabar tick paths; stop-first/one-target-per-bar
  assumptions are conservative conventions.
- Identity matching is structural, not historical provenance.
- The full historical experiment, including the holdout, remains unrun and
  requires separate owner execution instruction.
