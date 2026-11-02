# Task 1 — Targeted Strategy Verification

- **Date:** 2026-10-25
- **Repository:** `C:\xampp\htdocs\Trantor`
- **Scope:** Three targeted verification items only: MT5 timestamp provenance, a stronger future-aligned H4 pivot test, and `TradeSignal` completeness documentation.
- **Phase E status:** Planning only. No Phase E implementation, backtest, order submission, or execution was performed.

---

## 1. Executive Summary

| Area | Result | Summary |
|---|---|---|
| A — MT5 timestamp provenance | **UNVERIFIED** | The acquisition pipeline consistently treats MT5 candle epochs as UTC, but repository evidence does not independently prove that the raw epochs represent absolute UTC rather than broker server-time values. |
| B — Future-aligned H4 pivot leakage | **PASS** | Added two aligned-H4 regression tests. They verify two-right-bar confirmation and prove an appended future aligned pivot does not alter an earlier decision. |
| C — `TradeSignal` completeness | **PASS for research-only signalling; FAIL for complete execution management** | The detailed decision carries TP2 and management state; `TradeSignal` carries TP1 only and loses the complete plan. No interface change was made. |

The targeted test file and this report are the only files changed. No runtime strategy code was changed. The timestamp provenance issue remains a blocker for claiming absolute UTC validity; the `TradeSignal` interface remains a downstream execution limitation, not an execution feature.

---

## 2. Files Inspected

- `docs/strategy/phase_e_prerequisite_verification.md`
- `docs/strategy/phase_e_backtest_plan.md` (read only; not modified)
- `docs/strategy/phase_d_implementation_audit.md`
- `docs/strategy/xauusd_p_strategy_spec_v1.md`
- `src/trantor/strategy/xauusd_p.py`
- `tests/test_xauusd_p_strategy.py`
- `src/trantor/market_data/mt5_provider.py`
- `src/trantor/market_data/service.py`
- `tools/mt5_acquire_xauusd_p.py`
- `tools/mt5_readonly_probe.py`
- `src/trantor/models.py`
- Existing D1 metadata and validation files:
  - `research_data/xauusd_p/runs/20261022T194021Z/metadata.json`
  - `research_data/xauusd_p/runs/20261022T205633Z/metadata.json`
- `research_data/xauusd_p/alignment/20261022T205800Z/alignment.json`
- Existing cost metadata and spread files for D1/H4/M15
- `git status` and `git diff --check`

---

## 3. Timestamp Provenance Findings

### 3.1 What the code actually does

The acquisition path obtains MT5 candle records and converts the `time` field with:

```python
datetime.fromtimestamp(integer_index(value), tz=timezone.utc)
```

Evidence:

- `src/trantor/market_data/mt5_provider.py:268–277` — `_record_time`.
- `tools/mt5_acquire_xauusd_p.py:651–666` — canonical CSV writing calls `observed_at.astimezone(timezone.utc)`.
- `tools/mt5_acquire_xauusd_p.py:689–723` — acquisition validation requires UTC offsets and validates D1/H4/M15 boundary positions.
- The stored files use ISO-8601 UTC values such as `2024-01-02T00:00:00+00:00`.
- The alignment report states: “MT5 raw candle epochs are interpreted as UTC. The effective observed grid is UTC-aligned with no DST shift. The broker server timezone itself is not exposed by the Python package and remains unverified.”

### 3.2 Determinations

1. **MT5 API representation:** the code consumes an integer `time` field from the returned candle records. The repository does not independently document whether that integer is an absolute UTC epoch or a broker-server-wall-clock epoch. **UNVERIFIED.**
2. **Acquisition interpretation:** the code treats it as a Unix UTC epoch and writes UTC-normalized ISO timestamps. This is consistent code behavior, not independent proof.
3. **Justification for `datetime.fromtimestamp(..., timezone.utc)`:** **UNVERIFIED.** The implementation assumes UTC; the repository’s own alignment note says the broker server timezone is not exposed.
4. **Relevance of broker server timezone:** it is relevant if the MT5 epoch encodes server wall time; it would then affect candle alignment and decision/news/session timestamps. If MT5 returns a true absolute UTC epoch, server timezone would matter only for broker session questions. The available evidence does not distinguish these cases.
5. **Independent validation of stored UTC boundaries:** **NOT ESTABLISHED.** Validation confirms internal grids and UTC-labelled output, but the observed D1 midnight/H4 00/04/08/12/16/20/M15 quarter-hour grid is consistent with either a true UTC epoch or a server-time grid normalized as UTC. No independent known-UTC event or broker timezone evidence was found.
6. **Facts vs uncertainty:**
   - **Fact:** all stored candles are internally ordered, UTC-labelled, and aligned to the expected nominal grids.
   - **Fact:** acquisition and validation consistently apply the same UTC assumption.
   - **Unverified:** absolute epoch semantics, broker server timezone, and therefore absolute UTC provenance.

### 3.3 Classification

**UNVERIFIED.**

No concrete code defect was demonstrated, so no runtime or acquisition change was made. The correct next action is an independent timestamp provenance check, such as comparing a known UTC event/broker clock with a stored candle, or obtaining broker server-timezone evidence. Do not infer the timezone from the host computer.

---

## 4. Future-Aligned H4 Pivot Test

### 4.1 Timestamp convention established from code

`_close_time` adds the timeframe duration to `observed_at`:

```text
close_time = observed_at + timeframe duration
```

Evidence: `src/trantor/strategy/xauusd_p.py:84`; existing fixtures create H4 bars at 00:00, 04:00, 08:00, etc. Therefore `observed_at` is a bar **open** timestamp and a completed H4 bar closes four hours later.

### 4.2 New tests

Added to `tests/test_xauusd_p_strategy.py`:

- `test_future_aligned_h4_pivot_is_confirmed_only_after_two_right_bars` at line 475.
  - Builds aligned H4 bars at 00:00, 04:00, 08:00, 12:00, 16:00 and 20:00.
  - A high at the 08:00 bar is not present as a confirmed pivot with only one right-side bar.
  - With the 12:00 and 16:00 bars complete, the pivot at 08:00 becomes available, and `confirmed_at` is the close of the 16:00 bar (20:00).
- `test_aligned_future_h4_pivot_does_not_change_earlier_decision` at line 497.
  - Establishes the baseline candidate at the existing decision.
  - Appends correctly aligned future H4 bars at 12:00, 16:00, 20:00 and the next 00:00.
  - The 16:00 future bar contains a high that would contribute to a pivot if the implementation incorrectly included future data.
  - The changed decision preserves the same candidate and score as the baseline.

### 4.3 Result

**PASS.** The source uses only completed H4 bars for pivots, and the new tests establish both required right-bar confirmation and future-aligned invariance without relying on malformed or non-native timestamps.

No runtime strategy change was necessary.

---

## 5. TradeSignal Completeness

### 5.1 Detailed decision fields

`XAUUSDPSignalCandidate` contains:

- instrument, side, decision time, entry time and entry reference;
- selected zone cluster with bounds/source evidence;
- `TradePlan` with entry, stop, TP1, TP2, risk distance, 50/50 partial fractions, breakeven and cost-verification status;
- confidence and full confluence score;
- D1, H4, news, session and risk context;
- research mode, news-unaware status, rationale;
- `execution_ready=False`.

`StrategyDecision` additionally carries status, rejections, score breakdown and context.

### 5.2 Fields surviving conversion to `TradeSignal`

`XAUUSDPSignalCandidate.to_trade_signal` at `src/trantor/strategy/xauusd_p.py:572–588` produces the existing `TradeSignal` fields:

```text
signal_id
strategy_name
strategy_version
symbol
side
timeframe
generated_at
reference_price
stop_loss
take_profit
rationale
```

`take_profit` is TP1 only. TP2 remains in `candidate.plan.tp2` and appears in textual rationale, but is not a structured `TradeSignal` field.

### 5.3 Lost information

A consumer using only `TradeSignal` cannot safely reconstruct:

- entry timestamp/validity;
- TP2;
- TP1/TP2 partial fractions;
- breakeven transition and cost-verification state;
- zone and pivot context;
- score, confidence and rejection context;
- daily/H4/news/session/risk context;
- blocked monetary-sizing status;
- partial-exit feasibility status;
- `execution_ready`/research-only status.

The existing `TradeSignal` documentation correctly says it has no quantity, order type, broker identifier or submission state. That supports research-only use, but it does not make the signal a complete trade-management object.

### 5.4 Classification

- **PASS** — `TradeSignal` is suitable for research monitoring/notification of an entry, stop and TP1.
- **FAIL — complete execution management** — TP2 and partial-exit state cannot be recovered from `TradeSignal` alone.

No interface change was made. A future execution workflow would require, at minimum:

- explicit TP1 and TP2;
- entry validity/timestamp;
- partial fractions and breakeven rule;
- execution readiness;
- sizing/partial-volume status;
- provenance/decision identifier and context or a documented prohibition on consuming the bare signal.

---

## 6. Remaining Uncertainties and Blockers

1. **MT5 absolute timestamp provenance** remains UNVERIFIED.
2. The broker server timezone is not established in repository evidence.
3. A real economic calendar and verified XAUUSD-P session/holiday calendar are still unavailable.
4. `TradeSignal` remains incomplete for TP2/partial-exit management.
5. Contract economics, monetary sizing and partial-exit volume feasibility remain blocked.
6. Spread-adjusted cost treatment remains blocked pending a verified spread convention.
7. No Phase E implementation or backtest is authorized.

---

## 7. Files Changed

Only:

- `tests/test_xauusd_p_strategy.py` — added the two aligned-H4 regression tests.
- `docs/strategy/task_1_targeted_verification.md` — this report.

No runtime strategy code, strategy rules, Phase E plan, datasets, acquisition tools, broker settings, safety controls, or dependencies were changed.

---

## 8. Tests Run and Results

```text
$env:PYTHONPATH='src'; python -m pytest tests/test_xauusd_p_strategy.py -q
59 passed
```

```text
$env:PYTHONPATH='src'; python -m pytest -q
408 passed
```

```text
python -m compileall -q src tests
```

Passed with no output.

```text
git diff --check
```

No whitespace errors. Git reported only the pre-existing `README.md` LF/CRLF warning.

The tests are synthetic and deterministic; no live MT5 terminal or network was used.

---

## 9. Safety Confirmation

- No backtest was implemented or run.
- No order was submitted, modified, cancelled or simulated through a broker.
- No monetary sizing was calculated.
- No broker setting, safety control, execution code, account permission or trading mode was changed.
- No historical dataset or manifest was modified.
- The approved Phase E planning document was not modified.
- The strategy remains research-only and the read-only MT5 provider remains unchanged.

---

## 10. Recommendation

Run a separate timestamp-provenance verification using broker/server evidence before treating the stored candle timestamps as independently verified absolute UTC. Keep the new aligned-pivot tests as regression coverage.

For any future execution workflow, use the detailed `StrategyDecision`/`XAUUSDPSignalCandidate` rather than the bare `TradeSignal`, or obtain separate approval for an enriched signal interface carrying TP2 and partial-exit state.

**Stop here. Do not begin Task 2 or Phase E implementation without explicit authorization.**
