# Task 2 — Observed-Spread Verification

- **Date:** 2026-11-02
- **Repository:** `C:\xampp\htdocs\Trantor`
- **Instrument:** `XAUUSD-P` on the existing Fortune Prime MT5 demo environment
- **Scope:** Read-only verification of the stored observed-spread field and whether it supports a defensible future research cost convention.
- **Overall status:** **UNVERIFIED / BLOCKED for observed-spread adjustment.** Gross price/R remains the only justified baseline. No backtest or adjusted-performance calculation was performed.

---

## 1. Executive Summary

The repository preserves an observed spread value per candle for D1, H4 and M15. The acquisition code takes the MT5 `MqlRates.spread` value, converts it to an integer, calls it `spread_points`, and writes it to `spread_volume.csv` with `real_volume` and `tick_volume`.

The storage and timestamp alignment are verified. The **unit semantics, quote side, point-to-price conversion, and execution-price application are not verified**:

- There is no repository documentation or test proving that the MT5 field is in points.
- The metadata snapshot reports `point=0.01`, `digits=2`, `trade_tick_size=0.01`, and `spread=19`, but that is an instrument metadata snapshot, not an authoritative execution-price specification.
- Historical candle data contains OHLC only; there is no stored bid/ask basis or quote-side metadata.
- No evidence establishes whether the spread applies at entry, exit, both legs, or as a round-trip deduction.
- Zero spread values occur in all three timeframes and cannot be treated as zero transaction cost or as missing data without a stated convention.

Only the gross price/R baseline is justified at present. Spread-adjusted results remain blocked.

---

## 2. Files and Reports Inspected

Prior reports:

- `docs/strategy/phase_e_prerequisite_verification.md`
- `docs/strategy/task_1_targeted_verification.md`
- `docs/strategy/phase_e_backtest_plan.md` (read only; not modified)
- `docs/strategy/phase_d_implementation_audit.md`
- `docs/strategy/xauusd_p_strategy_spec_v1.md`

Source and acquisition code:

- `tools/mt5_acquire_xauusd_p.py`
- `src/trantor/market_data/mt5_provider.py`
- `src/trantor/market_data/service.py`
- `tools/mt5_readonly_probe.py`
- `src/trantor/models.py`

Data and metadata:

- D1: `research_data/xauusd_p/runs/20261022T205633Z`
- H4: `research_data/xauusd_p/runs/20261022T205633Z-01`
- M15: `research_data/xauusd_p/runs/20261022T205651Z`
- `candles.csv`, `spread_volume.csv`, `metadata.json` and `validation.json` in each run
- Existing D1 comparison run: `research_data/xauusd_p/runs/20261022T194021Z`
- Alignment report: `research_data/xauusd_p/alignment/20261022T205800Z/alignment.json`

No files were modified.

---

## 3. Stored Spread Field — Part A

### 3.1 Exact source field

The acquisition process obtains MT5 candle arrays containing `MqlRates`-style records and extracts:

```python
"spread_points": int(row["spread"])
```

Evidence:

- `tools/mt5_acquire_xauusd_p.py:400–415` — `copy_rates_range` result rows; `spread` converted to `int`.
- `tools/mt5_acquire_xauusd_p.py:880–895` — `spread_points`, `real_volume`, and `tick_volume` serialized to `spread_volume.csv`.
- `tools/mt5_acquire_xauusd_p.py:1220–1247` — cost rows are filtered to the exact UTC timestamps of completed candles.

The provider metadata path separately maps MT5 `info.spread` to `MT5SymbolMetadata.spread_points` at `src/trantor/market_data/mt5_provider.py:195–215`. This is a second, independent use of the name `spread_points`, but it still does not document the unit.

### 3.2 Data type and serialization

- Raw acquisition value: converted to `int`.
- CSV column name: `spread_points`.
- CSV serialization: `str(int(row["spread_points"]))`.
- `real_volume` and `tick_volume` are serialized with `repr(float(...))`.
- Header: `time_utc,spread_points,real_volume,tick_volume`.
- Example first row: `2024-01-02T00:00:00+00:00,23,0.0,144470.0`.

**Result:** **PASS** for source-field identification and serialization format.

### 3.3 Unit semantics

The code itself labels the value `spread_points`; that is evidence of programmer intent, not independent proof of the MT5 field's unit. No repository documentation, schema field, test, or metadata statement defines whether `row["spread"]` is:

- a number of price points;
- a price difference in currency units;
- another broker representation.

The MT5 `MqlRates.spread` field is the acquisition source, but the repository does not include authoritative unit documentation or a test that validates the raw field against a known point/price relationship.

**Result:** **UNVERIFIED.**

### 3.4 Per-candle capture and consistency

Actual stored files:

| Timeframe | Candle rows | Cost rows | Missing cost timestamps | Extra cost timestamps | Zero-spread rows |
|---|---:|---:|---:|---:|---:|
| D1 | 725 | 725 | 0 | 0 | 11 |
| H4 | 4,352 | 4,352 | 0 | 0 | 12 |
| M15 | 66,475 | 66,475 | 0 | 0 | 15 |

All three files use the same cost header and the same `_cost_rows_for_dataset` matching logic. Each completed candle has exactly one matching cost timestamp. Spread values range from 0 to 23 for D1/H4 and 0 to 71 for M15. `real_volume` is zero throughout.

`tick_volume` is MT5 tick volume, not exchange-traded volume. It is not used as evidence for spread units, quote side, or transaction cost.

**Result:** **PASS** for per-candle capture and D1/H4/M15 handling consistency.

**Limitation:** a zero value has no documented meaning. It must not be interpreted as free execution or silently dropped as missing. A future convention must define zero handling and missing-data handling separately.

### 3.5 Metadata confirmation

The authoritative D1/H4/M15 `metadata.json` instrument snapshots report:

```text
symbol: XAUUSD-P
digits: 2
point: 0.01
trade_tick_size: 0.01
spread: 19
contract size: 100.0
tick value: 1.0
```

These values are present in the repository, but they are broker metadata snapshots. No authoritative broker specification, account statement, or execution report confirms the meaning of `spread` or the complete price convention.

**Result:** **PASS for metadata presence; UNVERIFIED for unit/execution semantics.**

---

## 4. Point Size and Price Conversion — Part B

### 4.1 Verified presence

The metadata values listed in Part A are present. They establish that the broker snapshot describes:

- two decimal digits;
- a point value of 0.01;
- a tick size of 0.01;
- a reported symbol spread of 19.

### 4.2 Conversion assessment

A **proposed** conversion would be:

```text
spread_price = spread_points * 0.01
```

This is plausible only if:

1. the stored raw field is confirmed to be in points; and
2. `point=0.01` is confirmed as the authoritative price increment for the instrument.

Neither condition is independently verified by repository evidence. The presence of `point=0.01` and `digits=2` supports the intent, but does not prove that the MT5 `spread` field is in points or that the price basis is compatible.

**Result:** **UNVERIFIED.** No conversion was chosen as verified.

### 4.3 Missing evidence

- An authoritative instrument specification or broker statement defining point/tick/spread units.
- A raw MT5 sample with bid, ask, point, spread, and candle prices at the same instant.
- A documented mapping between the stored `spread_points` and a price difference.

---

## 5. Bid/Ask and Temporal Alignment — Part C

### 5.1 Historical candle price basis

The provider converts MT5 candle OHLC into `MarketObservation` using:

```text
open, high, low, close
```

Evidence: `src/trantor/market_data/mt5_provider.py:545–582`.

No bid/ask, midpoint, or quote-side field is stored. `MqlRates` candle data in this acquisition path does not include historical bid/ask prices. The `mt5_readonly_probe.py` can print current tick bid/ask at `448–462`, but no tick history or bid/ask history is stored in the research datasets.

**Result:** **UNVERIFIED.** The historical candle price basis is not established.

### 5.2 Temporal alignment

The cost rows are matched to candle timestamps by the same UTC epoch and exact ISO timestamp. Actual counts show one cost row per candle in all three timeframes with no missing or extra cost timestamps.

This proves timestamp alignment of the stored records. It does **not** prove:

- whether the spread is sampled at the bar open, close, or another instant;
- whether the spread is contemporaneous with the candle's high/low path;
- whether it is a bid-ask spread at all.

**Result:** **PASS for row timestamp alignment; UNVERIFIED for intra-bar/spread sampling semantics.**

### 5.3 Entry and exit price convention

The data cannot establish a defensible simulated entry/exit convention because:

- the candle price basis is unknown;
- the spread's quote side is unknown;
- the spread sampling time within each bar is unknown;
- it is unknown whether the spread should be charged at entry, exit, or both legs;
- gaps and same-bar stop/target events have no documented spread application rule.

**Result:** **UNVERIFIED / BLOCKED.**

### 5.4 Round-trip adjustment

A full round-trip adjustment cannot be calculated without double-counting or omission risk because the available evidence does not define:

- bid-side versus ask-side candle OHLC;
- whether the stored spread is already represented in the candle prices;
- entry/exit side adjustments;
- how many times to apply the spread.

**Result:** **BLOCKED.** No full round-trip cost can be claimed.

---

## 6. Recommended Research Treatment — Part D

Only the following treatment is justified now:

**Gross price/R baseline with no transaction-cost deduction and no claim that costs are included.**

Observed-spread sensitivity is **not permitted yet**. It may be implemented only after the missing evidence in §5 and §7 is resolved and the convention is documented and tested.

A hypothetical spread scenario is also not authorized by this verification. If one is later discussed, it must:

- be explicitly labelled a hypothetical assumption;
- identify the entry/exit side convention;
- state the point-to-price conversion;
- define zero/missing spread handling;
- state whether it is applied once per round trip or per leg;
- explain how double counting is avoided;
- avoid commission, financing, slippage, margin, and account-currency costs;
- not be presented as verified broker cost or net profitability.

No numerical spread assumption is made in this report.

---

## 7. Evidence Required to Resolve Remaining Blockers — Part E

Minimum evidence:

1. **Authoritative instrument specification:** broker or provider documentation defining `digits`, `point`, `trade_tick_size`, `trade_tick_value`, and `spread` units for XAUUSD-P.
2. **Raw quote sample:** a contemporaneous bid, ask, point, spread, and candle record at a known UTC time.
3. **Candle price basis:** documentation or paired data establishing whether OHLC is bid, ask, midpoint, or another basis.
4. **Spread sampling semantics:** whether the per-candle spread is at the open, close, or another point, and whether it is a snapshot or aggregate.
5. **Zero/missing policy:** explicit handling for observed zero spread and any missing cost row.
6. **Execution-cost convention:** exact entry/exit side adjustments and whether spread is applied once or twice.
7. **Double-counting test:** a fixture showing that applying the proposed convention changes a round-trip result exactly by the intended spread and no more.
8. **Gap/same-bar test:** behavior when a bar gaps through a stop or target and when stop/target ambiguity applies.

Future authorized tests should include:

- point-to-price conversion from authoritative metadata;
- bid/ask price-basis verification;
- cost timestamp alignment against candle timestamps;
- long/short entry/exit spread application;
- full round-trip reconciliation;
- zero-spread and missing-spread cases;
- no double counting in partial exits and breakeven.

None of these tests was implemented.

---

## 8. Consolidated Status Table

| Area | Result | Notes |
|---|---|---|
| A — source field identification | **PASS** | MT5 `spread` → int `spread_points` |
| A — serialization | **PASS** | Exact CSV header/row format verified |
| A — per-candle capture | **PASS** | 1:1 timestamp match for D1/H4/M15 |
| A — D1/H4/M15 consistency | **PASS** | Same code/header; no missing timestamps |
| A — unit semantics | **UNVERIFIED** | Variable name is intent, not proof |
| A — zero handling | **UNVERIFIED** | Zero values occur; meaning undocumented |
| B — metadata values present | **PASS** | point/digits/tick/spread present |
| B — point-to-price conversion | **UNVERIFIED** | No authoritative unit/point proof |
| C — OHLC price basis | **UNVERIFIED** | No bid/ask/midpoint evidence |
| C — row timestamp alignment | **PASS** | Exact timestamp match |
| C — intra-bar spread sampling | **UNVERIFIED** | No open/close sample evidence |
| C — entry/exit convention | **BLOCKED** | No quote-side or application rule |
| C — full round-trip adjustment | **BLOCKED** | Cannot establish without double counting/omission |
| D — gross R baseline | **PASS** | Only justified treatment |
| D — observed-spread sensitivity | **BLOCKED** | Requires evidence/convention/tests |
| D — hypothetical scenario | **UNVERIFIED / not authorized** | Must be labelled assumptions; no numeric assumption made |

**Overall:** observed-spread adjustment is **not ready**. The gross price/R baseline remains the only justified research treatment.

---

## 9. Exact Files Changed

Only:

```text
docs/strategy/task_2_spread_verification.md
```

No other file was changed.

The following were not modified:

- runtime code or tests;
- acquisition tools;
- historical datasets or manifests;
- broker settings or `DisabledBroker`;
- MT5 provider behavior;
- strategy rules;
- safety controls;
- dependencies;
- `docs/strategy/phase_e_backtest_plan.md`.

---

## 10. Safety and Execution Confirmation

- No backtest was implemented or run.
- No adjusted performance was calculated.
- No monetary position sizing was calculated.
- No order was submitted, modified, cancelled, or simulated through a broker.
- No broker interaction occurred.
- No new market data was acquired and no dataset was regenerated.
- The strategy remains research-only.

`git diff --check` was run and reported no whitespace errors; it showed only the pre-existing `README.md` LF/CRLF warning. Repository status showed the pre-existing `README.md` modification and untracked project directories; this task created only the standalone report.

---

## 11. Recommended Next Action

Keep Phase E gross price/R only.

Before any observed-spread scenario is authorized, obtain authoritative unit/point evidence and historical quote-side evidence, define zero/missing handling, document the entry/exit application rule, and add tests proving no double counting.

Do not implement the backtester or authorize Phase E implementation based on this report.

**Stop here.**
