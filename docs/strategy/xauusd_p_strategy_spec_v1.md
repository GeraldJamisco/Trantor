# XAUUSD-P Multi-Timeframe Strategy Specification

> **Implementation identity (note added 2026-11-04).** The rules below were
> approved during Phase D (see `phase_d_implementation_audit.md`) and are
> implemented without change as:
>
> | Identity | Strategy name | Version | Session filter | News | Used by |
> |---|---|---|---|---|---|
> | `xauusd_p_approved_v1` | `xauusd_p_multi_timeframe` | 1.0.0 | IANA London/NY overlap (active) | verified calendar, or labelled `news_unaware` in research/shadow/paper | live runtime, Phase G |
> | `xauusd_p_filter_isolated_v1` | `xauusd_p_multi_timeframe_research_filter_isolated` | 1.0.0 | isolated (not applied) | always `news_unaware` | Phase E/F baseline only |
>
> Phase E/F results belong to the filter-isolated variant and do not validate
> the approved strategy. The text below is the original Phase C document and its
> status line is historical.

- **Version:** 1.0-draft
- **Status:** Phase C proposal — awaiting review; not approved for implementation
- **Instrument:** `XAUUSD-P` only
- **Research timeframes:** D1, H4, M15
- **Implementation status:** Specification only. No strategy, sizing, backtest, or execution code exists.

---

## 1. Scope and Non-Negotiable Constraints

This specification defines the existing multi-timeframe gold strategy. It does not replace it with a
Donchian breakout or a moving-average-only system.

The strategy combines:

- D1 EMA-based market bias.
- H4 structural trend confirmation.
- H4 support/resistance and Fibonacci pullback zones.
- M15 RSI, reversal-candle, and tick-volume confirmation.
- An eight-point confluence score.
- Structural stops and partial take-profit management.
- Daily loss, entry-count, concurrent-position, cooldown, news, and session controls.

### Instrument boundary

`XAUUSD-P` is the designated broker-provided gold instrument. It is **not** GC or MGC, and no result
in this specification may be presented as a COMEX futures result.

### Mandatory safety boundary

This document does not authorize:

- monetary position sizing;
- broker order submission;
- demo or live execution;
- modifying `DisabledBroker`;
- changing paper-only safety controls;
- changing GC/MGC analysis or the COMEX calendar;
- adding dependencies.

---

## 2. Data and Decision Timing

### 2.1 Market data

| Timeframe | Role | Required data |
|---|---|---|
| D1 | Market bias | Completed OHLC + tick volume; EMA warm-up |
| H4 | Trend and zones | Completed OHLC + ATR/pivot history |
| M15 | Entry trigger | Completed OHLC + RSI/pattern/volume history |

Bar timestamps are UTC. The acquisition layer validates UTC normalization and native candle
boundaries. No strategy decision may use an unfinished D1, H4, or M15 candle.

### 2.2 Decision timestamp

For a signal evaluated on M15 bar `T`:

```text
decision_time = close_time(M15_T)
```

At `decision_time`, the strategy may use only:

- completed D1 bars whose close is at or before `decision_time`;
- completed H4 bars whose close is at or before `decision_time`;
- completed M15 bars through `T`;
- indicator values calculated only from those bars;
- confirmed pivots whose right-side confirmation bars are complete;
- a news calendar snapshot that was valid at `decision_time`.

### 2.3 Earliest simulated entry

```text
entry_time = open_time(M15_T+1)
entry_reference_price = open(M15_T+1)
```

If the next M15 bar is missing, cannot be validated, or the entry would be outside the approved
session, the candidate is rejected.

No fill at the trigger candle's close is permitted in historical simulation.

---

## 3. Parameter Table

All values marked **proposed** require explicit approval. None is claimed to be an empirical
optimum.

| Parameter | Proposed value | Meaning / trade-off |
|---|---:|---|
| `daily_ema_fast` | 50 | D1 fast EMA |
| `daily_ema_slow` | 200 | D1 slow EMA |
| `daily_ema_tolerance_pct` | 0.0005 (0.05% of latest close) | Defines “effectively equal” EMAs |
| `daily_min_bars` | 201 | EMA200 plus one prior completed bar |
| `d1_max_staleness_days` | 5 calendar days | Reject stale D1 context; must account for holidays |
| `h4_pivot_left` | 2 | Completed bars to the left of a pivot |
| `h4_pivot_right` | 2 | Completed bars required to confirm a pivot |
| `h4_atr_period` | 14 | ATR for zone widths, buffers, and stop limits |
| `h4_max_staleness_hours` | 72 | Reject stale H4 context |
| `sr_merge_atr` | 0.50 × H4 ATR14 | Merge nearby same-type pivots |
| `sr_zone_half_width_atr` | 0.25 × H4 ATR14 | S/R zone half-width |
| `zone_invalidation_atr` | 0.25 × H4 ATR14 | Close-beyond-zone invalidation buffer |
| `zone_max_age_h4` | 120 H4 bars | Retire stale zones (~20 days) |
| `fib_levels` | 0.500, 0.618 | Required retracement levels |
| `fib_band_atr` | 0.25 × H4 ATR14 | Proximity band around each Fib level |
| `fib_min_leg_atr` | 1.00 × H4 ATR14 | Minimum validated swing-leg size |
| `fib_max_age_h4` | 120 H4 bars | Retire stale Fibonacci zones |
| `m15_rsi_period` | 14 | Wilder RSI |
| `m15_rsi_long_threshold` | 30 | Strictly below 30 |
| `m15_rsi_short_threshold` | 70 | Strictly above 70 |
| `m15_rsi_window` | 1 completed bar | RSI must be true on the trigger candle |
| `engulf_min_body_fraction` | 0.20 of current range | Reject tiny-bodied engulfing patterns |
| `pin_wick_body_ratio` | 2.0 | Dominant wick at least twice the body |
| `pin_body_range_max` | 0.35 of range | Maximum body fraction |
| `pin_close_position` | 0.50 of range | Bullish close in upper half; bearish in lower half |
| `volume_lookback_m15` | 20 completed bars | Baseline excludes trigger bar |
| `volume_multiplier` | 1.50 | Trigger tick volume must be at least 1.5× median baseline |
| `m15_contiguous_bars` | 21 completed bars | Trigger plus prior 20 bars must be contiguous |
| `score_reject_below` | 5 | Below 5 = no trade |
| `score_high_confidence` | 7 | 7–8 = high confidence |
| `risk_high_confidence` | 0.010 of equity | Maximum 1.0% |
| `risk_medium_confidence` | 0.005 of equity | Maximum 0.5% |
| `daily_loss_limit` | 0.030 of equity | Stop new entries at -3% |
| `max_daily_entries` | 3 | New entries only |
| `max_concurrent_positions` | 2 | Active strategy positions |
| `cooldown_after_loss_m15` | 16 bars | 4 hours, proposed |
| `cooldown_after_two_losses_m15` | 32 bars | 8 hours, proposed |
| `stop_buffer_atr` | 0.25 × H4 ATR14 | Stop beyond structural reference |
| `max_stop_atr` | 3.00 × H4 ATR14 | Reject wider stops |
| `min_stop_ticks` | 1 | Reject zero/negative stop distance |
| `tp1_r` | 2.0R | First target |
| `tp2_r` | 3.0R | Second target before obstacle adjustment |
| `min_tp2_r` | 2.5R | Reject if obstacle leaves less |
| `target_obstacle_buffer_atr` | 0.25 × H4 ATR14 | Keep TP2 before opposing structure |
| `max_entry_distance_atr` | 0.50 × H4 ATR14 | Reject late/chased entries |
| `news_pre_minutes` | 30 | No new entry before high-impact event |
| `news_post_minutes` | 30 | No new entry after high-impact event |
| `news_score_window_minutes` | 60 | +1 only if no event in next 60 minutes |
| `news_calendar_max_age_minutes` | 15 | Calendar freshness requirement |
| `london_session` | 08:00–16:30 Europe/London | Proposed preferred session |
| `new_york_session` | 08:00–17:00 America/New_York | Proposed preferred session |
| `research_dev_period` | 2024-01-02..2024-12-31 | Development |
| `research_validation_period` | 2025-01-01..2025-12-31 | Validation |
| `research_holdout_period` | 2026-01-01..2026-10-21 | Holdout |

### 3.1 Fixed policy ceilings

These are not tuning parameters:

- high-confidence risk ceiling = 1.0%;
- medium-confidence risk ceiling = 0.5%;
- daily loss limit = -3%;
- maximum daily entries = 3;
- maximum concurrent positions = 2;
- maximum score = 8;
- score below 5 = no trade;
- H4 direction is mandatory;
- countertrend trades are disabled.

---

## 4. Daily Bias

### 4.1 Calculation

Use completed D1 closes only.

```text
EMA_fast_t = EMA(close, 50)
EMA_slow_t = EMA(close, 200)
```

EMA definition:

```text
k = 2 / (period + 1)
EMA_t = close_t * k + EMA_(t-1) * (1 - k)
```

The first EMA value is seeded with the simple mean of the first `period` completed closes.
EMA200 requires at least 200 completed D1 bars; `daily_min_bars=201` ensures a prior EMA state.

Tolerance:

```text
tol_t = 0.0005 * close_t
```

### 4.2 Classification

```text
BULLISH_D1: EMA_fast_t - EMA_slow_t >  tol_t
BEARISH_D1: EMA_slow_t - EMA_fast_t >  tol_t
NEUTRAL_D1: |EMA_fast_t - EMA_slow_t| <= tol_t
UNAVAILABLE: insufficient/stale/invalid D1 data
```

### 4.3 Previous-day high and low

The previous completed D1 high and low are recorded as **context only**:

- not a mandatory filter;
- not a score point;
- not used for entry timing;
- included in the candidate rationale and journal.

They do not override the EMA classification.

### 4.4 Daily role in candidate acceptance

| Daily state | Long candidate | Short candidate |
|---|---|---|
| Bullish | Eligible; +2 score | Reject: Daily conflict |
| Bearish | Reject: Daily conflict | Eligible; +2 score |
| Neutral | Eligible; 0 Daily points | Eligible; 0 Daily points |
| Unavailable/stale | Reject | Reject |

The “Daily conflict reject” is a proposed decision. It preserves the original two-point Daily
alignment while preventing trades against an established Daily trend. It must be approved before
implementation.

### 4.5 D1 failure behavior

- Missing, stale, non-finite, or incorrectly ordered D1 data: reject.
- Fewer than 201 completed D1 bars: reject.
- Latest completed D1 bar older than 5 calendar days: reject.
- No unfinished D1 candle may affect the bias.

---

## 5. H4 Trend

### 5.1 Pivot detection

Use completed H4 bars and H4 ATR14.

A pivot high at index `i` exists when:

```text
high_i > high_(i-1), high_i > high_(i-2),
high_i > high_(i+1), high_i > high_(i+2)
```

A pivot low is the inverse with `low`.

A pivot is confirmed only at the close of bar `i + 2`; it becomes usable for decisions after that
close. It must never be available earlier.

Proposed `pivot_left = 2`, `pivot_right = 2`.

### 5.2 H4 trend state

Let:

- `PH1` = most recent confirmed pivot high;
- `PH2` = previous confirmed pivot high;
- `PL1` = most recent confirmed pivot low;
- `PL2` = previous confirmed pivot low.

```text
BULLISH_H4: PH1 > PH2 and PL1 > PL2
BEARISH_H4: PH1 < PH2 and PL1 < PL2
NEUTRAL_H4: otherwise
UNAVAILABLE: fewer than 2 confirmed highs or 2 confirmed lows, or stale/invalid H4 data
```

### 5.3 Mandatory filter

- Long candidates require `BULLISH_H4`.
- Short candidates require `BEARISH_H4`.
- `NEUTRAL_H4` or `UNAVAILABLE` = reject.
- A candidate against H4 is rejected regardless of score.
- The countertrend exception is disabled.

### 5.4 H4 failure behavior

- Latest completed H4 bar older than 72 hours: reject.
- Insufficient pivot history: reject.
- Invalid ATR14 or incomplete confirmation bars: reject.
- Do not use a forming H4 bar.
- No H4 EMA is required; structural trend is the approved H4 definition.

---

## 6. H4 Trading Zones

### 6.1 Support and resistance zones

Pivot highs produce resistance zones; pivot lows produce support zones.

Zone calculation:

```text
center = mean(merged pivot prices)
half_width = 0.25 * H4_ATR14
lower = center - half_width
upper = center + half_width
```

Same-type pivots are merged when their centers are within:

```text
0.50 * H4_ATR14
```

Merged zone boundaries use the minimum lower and maximum upper boundary.

Validity:

- The zone must be confirmed before the decision time.
- The zone is invalidated for support when a completed H4 close < `lower - 0.25 * ATR14`.
- The zone is invalidated for resistance when a completed H4 close > `upper + 0.25 * ATR14`.
- A zone older than 120 H4 bars from its latest member confirmation is stale.
- Do not shift a zone boundary after the fact.

### 6.2 Fibonacci zones

Use the most recent completed H4 swing leg in the H4 trend direction:

- Bullish H4: most recent confirmed low `L` followed by confirmed high `H`.
- Bearish H4: most recent confirmed high `H` followed by confirmed low `L`.

Require:

```text
H - L >= 1.00 * H4_ATR14
```

Levels:

```text
Bullish: level_0.500 = H - 0.500 * (H - L)
Bullish: level_0.618 = H - 0.618 * (H - L)
Bearish: level_0.500 = L + 0.500 * (H - L)
Bearish: level_0.618 = L + 0.618 * (H - L)
```

The two levels are represented as one combined Fibonacci zone:

```text
lower = min(level_0.500, level_0.618) - 0.25 * H4_ATR14
upper = max(level_0.500, level_0.618) + 0.25 * H4_ATR14
```

Invalidation:

- Bullish leg invalid if H4 close < `L - 0.25 * ATR14`.
- Bearish leg invalid if H4 close > `H + 0.25 * ATR14`.
- Stale after 120 H4 bars or after a newer confirmed leg replaces it.
- Do not use future-known swings.

### 6.3 Zone eligibility and overlap

An M15 trigger candle is at a zone when:

```text
M15_low <= zone_upper and M15_high >= zone_lower
```

If several zones intersect the trigger:

1. Merge overlapping S/R and Fibonacci descriptions into one **zone cluster**.
2. Choose the cluster whose center is nearest the trigger close.
3. Award the H4-zone score once.

No extra points are awarded for multiple overlapping descriptions. The H4-zone contribution is
always exactly **+2**.

### 6.4 Order blocks and liquidity pools

Order blocks and liquidity pools are retained as intended future features but **deferred from the
v1 active strategy** because:

- they are not part of the approved eight-point score;
- no validated objective definition exists in the current evidence;
- adding them as extra score points would change the scoring system;
- using them without validation would risk subjective double-counting.

Future candidate definitions to review, not active in v1:

**Order-block candidate**

- Opposing H4 candle followed by displacement of at least 1.0 × H4 ATR14.
- The displacement must break the most recent confirmed H4 swing.
- Zone boundaries use the opposing candle body.
- Confirmed only after the structural break closes.
- Invalidated by a completed H4 close through the zone by 0.25 × ATR14.
- Expires after 60 H4 bars.

**Liquidity-pool candidate**

- At least two confirmed H4 highs or lows within 0.25 × H4 ATR14.
- Touches separated by at least 5 H4 bars.
- Confirmed only after the second pivot confirms.
- Invalidated by a completed H4 close through the pool by 0.25 × ATR14.
- Expires after 120 H4 bars.

These definitions require separate approval and cannot be used for scoring or entry in v1.

---

## 7. M15 Entry Conditions

### 7.1 RSI(14)

Use Wilder RSI(14) on completed M15 closes.

- Warm-up: at least 15 completed M15 bars.
- Long: RSI < 30 on the trigger candle.
- Short: RSI > 70 on the trigger candle.
- RSI equal to 30 or 70 does not qualify.
- `m15_rsi_window = 1`: the condition must be on the trigger candle, not an earlier candle.
- Missing/invalid RSI: withhold the RSI point; do not reject solely for that unless the final score
  is below 5.

### 7.2 Engulfing patterns

Let `body_prev = abs(close_prev - open_prev)` and `body_cur = abs(close_cur - open_cur)`.

**Bullish engulfing**

```text
close_prev < open_prev
close_cur > open_cur
open_cur <= close_prev
close_cur >= open_prev
body_cur >= 0.20 * (high_cur - low_cur)
high_cur > low_cur
```

**Bearish engulfing**

```text
close_prev > open_prev
close_cur < open_cur
open_cur >= close_prev
close_cur <= open_prev
body_cur >= 0.20 * (high_cur - low_cur)
high_cur > low_cur
```

Engulfing is defined on candle **bodies**, not full ranges.

### 7.3 Pin bars

Definitions:

```text
range = high - low
body = abs(close - open)
lower_wick = min(open, close) - low
upper_wick = high - max(open, close)
```

**Bullish pin**

```text
range > 0
body > 0
lower_wick >= 2.0 * body
body <= 0.35 * range
close >= low + 0.50 * range
upper_wick <= 0.35 * range
```

**Bearish pin**

```text
range > 0
body > 0
upper_wick >= 2.0 * body
body <= 0.35 * range
close <= high - 0.50 * range
lower_wick <= 0.35 * range
```

Zero-range or zero-body candles do not qualify.

### 7.4 Tick-volume confirmation

Use `tick_volume` only. This is not exchange-traded volume.

```text
baseline = median(tick_volume of the 20 completed M15 bars preceding the trigger)
trigger_volume = tick_volume of the trigger candle
confirm = trigger_volume >= 1.50 * baseline
```

Rules:

- The trigger candle is excluded from the baseline.
- Fewer than 20 prior bars: no volume point.
- Missing, zero, negative, or non-finite baseline or trigger volume: no volume point.
- No other symbol’s volume may be substituted.
- The baseline uses only prior completed bars.

### 7.5 Entry trigger and late-entry filter

Mandatory conditions:

1. D1 data available and not conflicting with the candidate direction.
2. H4 trend aligned.
3. Valid H4 S/R or Fibonacci zone intersects the trigger range.
4. M15 trigger candle is complete.
5. The last 21 M15 bars are contiguous on the 15-minute grid.
6. Score >= 5.
7. News safety filter passes.
8. Session filter passes.
9. Risk, cooldown, daily-entry, and concurrent-position checks pass.
10. Next M15 bar is available.

Earliest entry is the next M15 open.

Late-entry rejection:

```text
Long:  next_open > zone_upper + 0.50 * H4_ATR14  => reject
Short: next_open < zone_lower - 0.50 * H4_ATR14 => reject
```

Also reject if the next open has moved more than 0.50 × H4 ATR14 from the trigger close in the
direction of the trade.

---

## 8. Eight-Point Confluence Score

| Factor | Points | Condition |
|---|---:|---|
| Daily alignment | +2 | D1 bias equals candidate direction |
| H4 zone | +2 | Valid S/R or Fibonacci zone intersects trigger; awarded once |
| RSI | +1 | RSI < 30 long / > 70 short on trigger |
| Reversal candle | +1 | Valid engulfing or pin bar |
| Tick-volume confirmation | +1 | Trigger volume >= 1.5× prior 20-bar median |
| News | +1 | Verified calendar: no high-impact USD event in next 60 minutes |
| **Maximum** | **8** | |

H4 trend is mandatory and **not scored**.

### 8.1 Missing evidence

A missing input never earns a point:

- no Daily alignment evidence: 0 Daily points;
- no RSI: 0 RSI points;
- no pattern: 0 pattern points;
- no volume: 0 volume points;
- no verified calendar: 0 news points.

A missing input does not automatically earn a rejection, except for mandatory filters and the
score floor. The final score decides.

### 8.2 Confidence tiers

| Score | Classification | Maximum risk |
|---:|---|---:|
| 7–8 | High | 1.0% equity |
| 5–6 | Medium | 0.5% equity |
| 0–4 | Reject | 0% |

A score of 7/8 is **not** a probability of winning. It is the count of satisfied rule components.

### 8.3 Mandatory filters override score

Reject regardless of score when:

- H4 is neutral, opposite, or unavailable;
- D1 is opposite or unavailable;
- price is not at a valid H4 zone;
- session is outside the approved window;
- news safety fails or the calendar cannot be verified in production;
- score < 5;
- risk cannot be calculated or exceeds the ceiling;
- cooldown, daily loss, daily entry, or concurrent-position limit applies;
- data completeness fails.

---

## 9. Stop Loss

### 9.1 Structural reference

For a long:

```text
structural_reference = min(eligible_zone.lower, most_recent_confirmed_H4_swing_low)
stop = structural_reference - 0.25 * H4_ATR14
```

For a short:

```text
structural_reference = max(eligible_zone.upper, most_recent_confirmed_H4_swing_high)
stop = structural_reference + 0.25 * H4_ATR14
```

The stop is rounded away from entry to the instrument tick size (`0.01`):

- long stop: round down;
- short stop: round up.

### 9.2 Stop constraints

- `stop_distance = abs(entry - stop)`.
- Must be greater than or equal to `min_stop_ticks * tick_size`.
- Must be less than or equal to `3.0 * H4_ATR14`.
- If no valid zone or structural swing exists: reject.
- If the stop would need to be moved inside the zone to satisfy risk: reject.
- If stop distance is too large: reject; never tighten the stop arbitrarily.

---

## 10. Take Profit and Position Management

### 10.1 Targets

Let `R = abs(entry - stop)`.

```text
TP1 = entry + 2.0R (long) / entry - 2.0R (short)
TP2 = entry + 3.0R (long) / entry - 3.0R (short)
```

Targets are rounded conservatively toward entry to the instrument tick size.

### 10.2 Structural obstacle rule

Find the nearest confirmed H4 opposing swing level beyond entry:

- long: nearest confirmed swing high above entry;
- short: nearest confirmed swing low below entry.

If the obstacle is before TP1:

- reject; do not place TP1 beyond a nearby opposing structure.

If the obstacle lies between TP1 and TP2:

```text
Long:  TP2 = obstacle - 0.25 * H4_ATR14
Short: TP2 = obstacle + 0.25 * H4_ATR14
```

Require `TP2 >= 2.5R`. If not, reject.

If no obstacle exists before TP2, keep `TP2 = 3.0R`.

### 10.3 Partial exits

- TP1 closes 50% of the original position.
- TP2 closes the remaining 50%.
- Original volume must be at least `2 * volume_step`; otherwise reject because 50/50 partial
  execution is impossible.
- Round volume down to the valid step; never round upward.
- Partial fills remain part of the same position and do not count as new entries.

### 10.4 Breakeven after TP1

After cumulative TP1 fill reaches at least 50% of the original volume:

```text
breakeven = entry
```

If a verified round-trip cost per unit is available, shift breakeven in the favorable direction by
that cost. Until costs are verified, breakeven remains at entry and the journal records
`breakeven_costs_verified=false`.

### 10.5 Same-bar and gap assumptions

For historical simulation:

- If a bar could have touched both stop and TP1/TP2, assume the **stop executes first**.
- Gap through stop: fill at the bar open if it is worse than the stop.
- Gap through a target: use the target price for conservative accounting; do not credit a better
  gap price.
- A target “touch” is not automatically an execution in live trading. Research simulation may
  assume a fill at the target only under the declared conservative model.
- If TP1 is not filled, TP2 is inactive.
- If the remaining position is stopped at breakeven, the trade is flat, not a new trade.

---

## 11. Risk Policy

### 11.1 Risk ceilings

- High confidence (7–8): maximum 1.0% of account equity.
- Medium confidence (5–6): maximum 0.5% of account equity.
- Below 5: no trade.

Risk is measured from the entry to the structural stop, including expected costs once verified.

### 11.2 Monetary sizing is blocked

The following broker/account evidence is required before monetary sizing:

- exact contract-size convention;
- exact monetary interpretation of tick value;
- value per price unit in account currency;
- account-currency conversion;
- volume minimum, maximum, and step;
- margin requirement;
- commission;
- financing/swap;
- slippage and stop-level rules.

Until verified, no monetary size may be calculated or claimed.

Future formula, not authorized for implementation:

```text
risk_amount = equity * risk_fraction
risk_per_unit = stop_distance * verified_value_per_price_unit
raw_quantity = risk_amount / risk_per_unit
quantity = round_down_to_step(raw_quantity)
```

Reject if:

- quantity < volume_min;
- quantity < 2 * volume_step (partial exits impossible);
- margin cannot be verified;
- costs cannot be represented;
- calculated risk exceeds the confidence ceiling.

### 11.3 Daily loss limit

Hard limit: **-3%**.

Proposed research boundary: **00:00 UTC**. Live/demo boundary remains blocked until the
XAUUSD-P trading-day and session calendar are verified.

Measure:

```text
daily_loss_fraction =
    (start_of_day_equity - current_equity) / start_of_day_equity
```

Include realized P&L, unrealized P&L, and known costs. If costs are unknown, record
`cost_adjusted=false`; do not claim a cost-verified limit.

When the limit is reached:

- stop opening new positions;
- record the lockout reason;
- continue managing existing positions under their approved stops/targets;
- do not resume because a new signal appears;
- reset only at the next approved trading-day boundary.

### 11.4 Daily entries and concurrent positions

- Maximum 3 new entries per trading day.
- Count actual new entries, including later breakeven/loss exits.
- Do not count partial exits or stop/target management as new entries.
- Maximum 2 active strategy positions.
- Position identity is the broker/position ID plus the originating signal/order ID.
- Partial exits remain part of the original position.
- Prevent duplicates with an idempotency key and a persisted position/signal registry.

### 11.5 Cooldown

Proposed:

- A losing trade triggers a cooldown of **16 completed M15 bars** (4 hours).
- Two consecutive losing trades trigger **32 completed M15 bars** (8 hours).
- Cooldown starts at the exit timestamp.
- Scope: XAUUSD-P only.
- Daily loss lockout supersedes cooldown.
- Persist cooldown state across restarts. If state is missing, fail closed until reconciled.

A losing trade is one whose final realized net result is negative. Until costs are verified, use
gross R < 0 for research and label the trade `cost_adjusted=false`. Breakeven is not a loss.

### 11.6 Prohibited behaviors

- Martingale or recovery sizing.
- Increasing risk after losses.
- Averaging down without separate approval.
- Pyramiding without separate approval.
- Fixed-lot sizing unrelated to stop distance.
- Bypassing daily loss, daily entry, concurrent-position, cooldown, session, or news controls.
- Entering when monetary risk or partial-exit feasibility is unknown.

---

## 12. News and Sessions

### 12.1 News policy

Required calendar capabilities:

- UTC event timestamps.
- Currency code.
- Impact classification.
- Revision/update timestamps.
- Source/version identifier.
- Historical coverage for backtests.

High-impact USD event set for v1:

- US Nonfarm Payrolls.
- FOMC decisions and the relevant press conference.
- US Consumer Price Index.

Safety filter:

- No new entries from 30 minutes before to 30 minutes after a qualifying event.
- This is separate from the +1 score condition.

Score condition:

- +1 only if a verified calendar shows no qualifying high-impact USD event in the next 60 minutes.

Fail-safe:

- **Production/demo:** if the calendar is unavailable, stale, incomplete, or failed, no new entries.
- **Historical research:** a clearly labelled `news_unaware` variant may run without the +1 point
  and without the 30-minute pause, but its results cannot claim news-filter compliance or live
  readiness.
- Never infer “no news” from missing data.

Staleness:

- Calendar feed snapshot max age: 15 minutes.
- Revised event times replace prior times.
- Timezone conversion is performed by the calendar adapter to UTC; DST must be handled by the
  source, not by a fixed offset in the strategy.

### 12.2 Sessions

Preferred overlap is computed from IANA zones, not fixed UTC:

```text
London:   08:00–16:30 Europe/London
New York: 08:00–17:00 America/New_York
overlap = intersection of both local windows, converted to UTC per date
```

Only entries within the overlap are eligible.

XAUUSD-P’s actual session and holiday calendar are unverified. Therefore:

- Research may use the IANA overlap with `session_calendar_verified=false`.
- Production/demo entries are blocked until the broker’s XAUUSD-P session calendar is verified.
- Do not assume COMEX hours.
- Do not hardcode UTC after DST changes.

### 12.3 Optional macro context

DXY and US 10-year Treasury yields are optional future research inputs.

- Not in the eight-point score.
- Not mandatory filters.
- No verified long-history source is currently available.
- No source or coverage may be invented.

---

## 13. Missing, Stale, and Invalid Data

| Input | Missing/stale/invalid behavior |
|---|---|
| D1 candles | Reject candidate |
| D1 EMA warm-up | Reject candidate |
| H4 candles | Reject candidate |
| H4 ATR/pivot warm-up | Reject candidate |
| H4 trend unavailable | Reject candidate |
| H4 zone invalid/stale | Reject candidate |
| M15 trigger candle | Reject candidate |
| M15 last 21 bars not contiguous | Reject candidate |
| RSI unavailable | Withhold +1; reject if score <5 |
| Pattern unavailable | Withhold +1; reject if score <5 |
| Tick volume unavailable/invalid | Withhold +1; reject if score <5 |
| Spread unavailable | Signal may be researched; cost model incomplete; execution blocked |
| News calendar unavailable | Production: reject; research: labelled news-unaware mode only |
| Session calendar unavailable | Production: reject; research: IANA overlap only |
| Instrument metadata unavailable | Research signal may be generated; monetary sizing/execution blocked |
| Cost inputs unavailable | Backtest sensitivity only; no net-profitability claim; execution blocked |
| Unexplained gap in trigger window | Reject candidate |

No missing value is replaced with zero, carried forward, or treated as confirmation.

---

## 14. Future Backtesting Requirements

No backtest engine is implemented in Phase C.

A later engine must:

- use only information available at each decision timestamp;
- use completed D1/H4/M15 candles;
- enter at the next M15 open after the signal;
- respect the contiguous-M15-trigger-window rule;
- apply spread from the separate cost dataset, with its limitations;
- keep commission, financing, slippage, and margin as explicit scenarios;
- assume stop-first when a bar could touch both stop and target;
- fill gap-through-stop at the open;
- use conservative target-fill assumptions;
- simulate TP1/TP2, partial fills, and breakeven management;
- prevent duplicate trades and impossible fills;
- split chronologically:
  - development: 2024;
  - validation: 2025;
  - holdout: 2026 through 2026-10-21;
- never shuffle time-series data;
- report insufficient samples as inconclusive;
- report total return, annualized return where meaningful, maximum drawdown, trade count, win rate,
  average win/loss, average R, expectancy, profit factor, gross profit/loss, costs, exposure,
  holding duration, yearly results, confidence-tier results, long/short results, pre/post-cost
  results, daily trade-count distribution, no-trade days, daily-loss-limit days, rejected setups,
  and rule-compliance statistics.

A positive historical result does not establish profitability or execution readiness.

---

## 15. Deferred Features and Blockers

### Deferred from v1 active strategy

- Order blocks — candidate definition only; no score or entry use.
- Liquidity pools — candidate definition only; no score or entry use.
- DXY and US10Y context — optional future research, no score.
- News-calendar integration — required before production, unavailable now.
- Monetary position sizing — blocked by unverified contract economics.
- Broker execution — entirely out of scope.

### Explicit blockers

- Contract-size/tick-value/margin/commission/financing verification.
- XAUUSD-P session and holiday calendar.
- Verified economic-calendar source and historical coverage.
- Cost-verified breakeven and net performance.
- User approval of all proposed parameters and interpretation decisions.

---

## 16. Approval Status

This specification is a **draft for review**, not an approved implementation contract.

Implementation may begin only after the decision report’s approval checklist is resolved and
Phase D is explicitly authorized.
