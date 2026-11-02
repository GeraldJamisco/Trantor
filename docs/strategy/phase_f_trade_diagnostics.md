# Phase F Trade-Level Diagnostics

**GROSS BASELINE - EXECUTION COSTS EXCLUDED**

- **Source:** `research_results/xauusd_p/phase_e_authoritative_20261102/`
- **Diagnostic artifacts:** `research_results/xauusd_p/phase_f_diagnostics/`
- **Run identity:** `3da62cac42336920f9564088f7e6be21915d0f53d51a64982aa7a7dea2de1ad2`
- **Scope:** 43 closed simulated trades from the research-isolated XAUUSD-P variant.
- **Status:** Diagnostic only. No strategy, dataset, backtest, broker, account, or execution change was made. No second backtest was launched.

## 1. Verification

The diagnostic service verified before calculating:

- the authoritative `trantor.research_decision` run contract;
- 43/43 trade-linked canonical decision records and canonical hashes;
- 43/43 entry attempts linked to their decisions;
- all four source dataset SHA-256 checksums against the approved defaults;
- the summary/manifest run identity and record contract;
- the summary result of 43 closed trades.

Result:

```text
run_verification=PASS
trade_linkage=43/43
attempt_linkage=43/43
source_checksums=4/4 verified
```

The original Phase E artifacts were not modified. The Phase F calculations read the M15/D1/H4 validated datasets and write only to the separate `phase_f_diagnostics` directory.

## 2. Reconciliation

| Check | Result |
|---|---|
| Overall closed trades | 43 |
| Wins / losses / breakeven | 13 / 30 / 0 |
| Realized R | -5.00R |
| Expectancy | -0.1163R/trade |
| Profit factor | 0.8333 |
| Maximum drawdown | 17.00R |
| TP1 partial events | 13 |
| TP2 completions | 8 |
| Breakeven-stop exits after TP1 | 5 |
| Initial-stop exits | 30 |
| Ambiguous stop-first events | 6 |
| Open or truncated trades | 0 |

The 17R drawdown is the peak-to-trough cumulative realized-R drawdown of closed trades ordered by exit time and then trade ID:

- peak cumulative R: +10.00R at trade 19;
- trough cumulative R: -7.00R after trade 38;
- peak-to-trough: 17.00R.

This is price/R research risk, not monetary account drawdown.

## 3. Performance by split

**GROSS BASELINE - EXECUTION COSTS EXCLUDED**

| Split | Trades | Wins / losses | Win rate | Realized R | Expectancy | Profit factor | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024 Development | 17 | 9 / 8 | 52.94% | +8.50R | +0.5000R | 2.0625 | 4.00R |
| 2025 Validation | 21 | 2 / 19 | 9.52% | -15.50R | -0.7381R | 0.1842 | 17.00R |
| 2026 Holdout | 5 | 2 / 3 | 40.00% | +2.00R | +0.4000R | 1.6667 | 3.00R |
| Overall | 43 | 13 / 30 | 30.23% | -5.00R | -0.1163R | 0.8333 | 17.00R |

Every split has fewer than 30 closed trades and is **inconclusive**. The five-trade holdout is not reliable evidence. The overall sample is descriptive, not statistical proof.

## 4. Breakdowns

### Direction

| Group | Trades | Wins / losses | Realized R | Expectancy | PF |
|---|---:|---:|---:|---:|---:|
| Buy | 42 | 13 / 29 | -4.00R | -0.0952R | 0.8621 |
| Sell | 1 | 0 / 1 | -1.00R | -1.0000R | 0.0000 |

The single short trade is too small a sample for a direction comparison.

### Confluence score

| Score | Trades | Wins / losses | Realized R | Expectancy | PF |
|---|---:|---:|---:|---:|---:|
| 5 | 27 | 10 / 17 | +2.00R | +0.0741R | 1.1176 |
| 6 | 16 | 3 / 13 | -7.00R | -0.4375R | 0.4615 |

Both score groups are below 30 trades. The data do not support a claim that a higher score improved outcomes in this run.

### Exit classification

| Classification | Trades | Wins / losses | Realized R | Expectancy |
|---|---:|---:|---:|---:|
| Initial stop | 30 | 0 / 30 | -30.00R | -1.0000R |
| Breakeven after TP1 | 5 | 5 / 0 | +5.00R | +1.0000R |
| TP2 completed | 8 | 8 / 0 | +20.00R | +2.5000R |

### TP1 and TP2

| Condition | Trades | Realized R | Expectancy |
|---|---:|---:|---:|
| TP1 reached | 13 | +25.00R | +1.9231R |
| TP1 not reached | 30 | -30.00R | -1.0000R |
| TP2 completed | 8 | +20.00R | +2.5000R |
| TP2 not completed | 35 | -25.00R | -0.7143R |

### D1/H4 alignment

| Alignment | Trades | Realized R | Expectancy |
|---|---:|---:|---:|
| Aligned long: D1 bullish + H4 bullish | 42 | -4.00R | -0.0952R |
| Aligned short: D1 bearish + H4 bearish | 1 | -1.00R | -1.0000R |

All 43 trades were aligned with the recorded D1/H4 direction. There were no countertrend or mixed-alignment trades, so this dataset cannot measure an alignment effect.

### Recorded confluence components

| Component | Awarded: trades / R | Not awarded: trades / R |
|---|---:|---:|
| Daily alignment | 43 / -5.00R | 0 / 0R |
| H4 zone | 43 / -5.00R | 0 / 0R |
| RSI | 25 / +2.00R | 18 / -7.00R |
| Reversal candle | 8 / -6.00R | 35 / +1.00R |
| Tick-volume confirmation | 26 / -8.00R | 17 / +3.00R |
| News | 0 / 0R | 43 / -5.00R |

These are observational groups with small samples; no factor is established as a cause.

## 5. 2025 validation focus

The validation split contained 21 long trades. All 21 had bullish D1 and bullish H4. It produced two wins and 19 losses:

- trade 19: TP2 completed, +2.50R;
- trade 28: TP1 reached, then breakeven stop, +1.00R;
- the other 19 trades: initial stop, -1.00R each.

Only 2 of 21 validation trades reached TP1. No validation trade that failed to reach TP1 avoided a loss. This is a descriptive observation, not a causal explanation.

### Validation trade summary

MFE/MAE are derived bar-level approximations, not exact intrabar excursions. Risk is price distance, not money.

| ID | Date | Score | Risk | Exit | R | MFE / MAE (R) | TP1 / TP2 | Ambiguous |
|---:|---|---:|---:|---|---:|---:|---|---:|
| 18 | 2025-01-13 | 6 | 0.11 | stopped | -1.00 | 12.36 / 31.91 | no / no | 1 |
| 19 | 2025-01-27 | 5 | 0.37 | tp2_closed | +2.50 | 15.92 / 0.14 | yes / yes | 0 |
| 20 | 2025-02-12 | 5 | 16.89 | stopped | -1.00 | 0.37 / 1.51 | no / no | 0 |
| 21 | 2025-02-12 | 5 | 18.54 | stopped | -1.00 | 0.24 / 1.47 | no / no | 0 |
| 22 | 2025-04-03 | 6 | 4.77 | stopped | -1.00 | 0.87 / 1.61 | no / no | 0 |
| 23 | 2025-04-14 | 5 | 12.82 | stopped | -1.00 | 0.31 / 1.02 | no / no | 0 |
| 24 | 2025-04-14 | 5 | 2.46 | stopped | -1.00 | 3.66 / 1.12 | no / no | 1 |
| 25 | 2025-04-23 | 6 | 13.91 | stopped | -1.00 | 0.09 / 1.23 | no / no | 0 |
| 26 | 2025-04-25 | 5 | 16.04 | stopped | -1.00 | 0.31 / 1.70 | no / no | 0 |
| 27 | 2025-04-25 | 5 | 10.76 | stopped | -1.00 | 0.42 / 2.04 | no / no | 0 |
| 28 | 2025-05-27 | 6 | 2.86 | breakeven_stop | +1.00 | 2.24 / 4.32 | yes / no | 0 |
| 29 | 2025-05-27 | 5 | 3.63 | stopped | -1.00 | 0.73 / 2.31 | no / no | 0 |
| 30 | 2025-09-17 | 5 | 6.78 | stopped | -1.00 | 0.61 / 1.01 | no / no | 0 |
| 31 | 2025-09-24 | 6 | 11.75 | stopped | -1.00 | 0.33 / 1.11 | no / no | 0 |
| 32 | 2025-09-24 | 5 | 10.04 | stopped | -1.00 | 0.05 / 1.12 | no / no | 0 |
| 33 | 2025-10-09 | 5 | 13.95 | stopped | -1.00 | 0.02 / 1.60 | no / no | 0 |
| 34 | 2025-10-21 | 6 | 53.10 | stopped | -1.00 | 0.14 / 1.09 | no / no | 0 |
| 35 | 2025-10-22 | 5 | 30.48 | stopped | -1.00 | 0.87 / 1.03 | no / no | 0 |
| 36 | 2025-11-14 | 6 | 7.32 | stopped | -1.00 | 0.76 / 4.50 | no / no | 0 |
| 37 | 2025-12-08 | 6 | 0.44 | stopped | -1.00 | 33.82 / 4.61 | no / no | 1 |
| 38 | 2025-12-24 | 6 | 1.10 | stopped | -1.00 | 7.17 / 4.27 | no / no | 1 |

Validation observations:

- 19 of 21 trades exited at the initial stop.
- Validation median MFE was 0.61R, compared with 2.73R in development and 1.81R in holdout.
- 15 of 19 validation losers had bar-level MFE below 1R.
- Four validation losers had large bar-level MFE but were the four ambiguous stop-first trades; the stop-first convention assigns -1R and does not assume the target path.
- Nine losses exited after one M15 bar; three more exited after two bars.
- Risk distances ranged from 0.11 to 53.10 price units; the median was 10.76. Six losses had risk distance at or below 5 price units.
- Score 6 validation trades were 9 trades / -7.00R; score 5 were 12 trades / -8.50R. Neither group is large enough to establish a score effect.
- Reversal-candle-awarded validation trades were 5 trades / 0 wins / -5.00R. RSI not awarded was 10 trades / 0 wins / -10.00R. Tick-volume awarded was 14 trades / 1 win / -12.00R. These subgroup sizes are small and should be treated as hypotheses, not causes.

## 6. Ambiguous stop-first handling

Six trades contain ambiguous bars: trades 7 and 8 in development, and 18, 24, 37, and 38 in validation.

| Trade | Split | Event | Bar touched TP1 | Bar touched TP2 | Realized |
|---:|---|---|---|---|---:|
| 7 | development | initial stop | yes | yes | -1.00R |
| 8 | development | breakeven stop after TP1 | yes | yes | +1.00R |
| 18 | validation | initial stop | yes | yes | -1.00R |
| 24 | validation | initial stop | yes | yes | -1.00R |
| 37 | validation | initial stop | yes | yes | -1.00R |
| 38 | validation | initial stop | yes | yes | -1.00R |

On these bars the OHLC data cannot establish whether the stop or the target was reached first. The backtester conservatively applies stop-first. It does not claim an alternative target-first outcome or compute counterfactual profit.

## 7. Confirmed observations versus hypotheses

### Confirmed from the records

- The 2025 validation split lost 15.50R over 21 trades; 19 were initial-stop losses and two were winners.
- The 17R maximum drawdown is confirmed by the closed-trade sequence: +10R peak at trade 19 to -7R trough after trade 38.
- The 13 TP1 events, 8 TP2 completions, and 5 breakeven exits reconcile exactly with the trade records.
- Six ambiguous stop-first events exist; all six bars touched both TP1 and TP2 under bar-level OHLC.
- The validation split had only two TP1-reaching trades; both were non-losses.
- Every trade was aligned with the recorded D1 and H4 state; no alignment contrast exists in this run.

### Hypotheses, not established causes

- The poor validation result may be associated with weak follow-through: 15 of 19 losers had bar-level MFE below 1R, and validation median MFE was only 0.61R.
- Stop placement may be vulnerable to normal bar volatility, especially where risk distance was very small and stop-first ambiguity occurred. The data show the coincidence but do not establish causation.
- Score 6 may have underperformed score 5 in this sample, but the groups are small and this is not a basis for changing thresholds.
- Reversal-candle, RSI, and tick-volume component results are observational only; their subgroup sizes are inadequate for factor selection.
- Missing verified session/news calendars and execution costs may hide relevant event or cost effects; those effects cannot be evaluated from this run.
- An alternative exit policy, stop width, or entry timing could change results, but no such change is justified or tested here.

## 8. Constraints

- The experiment used the research-isolated filter variant, not the complete intended strategy.
- MFE/MAE are derived from M15 bar highs/lows from the entry bar through the final exit-event bar. They are bar-level approximations and include the possibility of post-exit movement on the final bar.
- The analysis is gross price/R only. Spread, commission, financing, slippage, monetary sizing, and account-equity returns are excluded.
- Absolute UTC provenance, session compliance, and news compliance are unverified.
- Small samples are descriptive only; no statistical significance is claimed.
- No strategy optimization or parameter change was performed.

## 9. Artifacts

```text
research_results/xauusd_p/phase_f_diagnostics/trade_diagnostics.json
research_results/xauusd_p/phase_f_diagnostics/trade_diagnostics.csv
research_results/xauusd_p/phase_f_diagnostics/breakdowns.json
research_results/xauusd_p/phase_f_diagnostics/summary.json
```

The JSON/CSV trade rows distinguish recorded fields from derived fields. The full decision hashes, D1/H4 context, score-factor details, exit events, MFE/MAE, and ambiguous-event reconstruction are retained there.
