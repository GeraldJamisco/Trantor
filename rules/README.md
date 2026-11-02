# TRANTOR: GOLD MULTI-TIMEFRAME TRADING STRATEGY

## Strategy Specification, Professional Trading Discipline, and Phased Implementation Instructions

### 1. Your Role

You are the senior quantitative developer responsible for implementing the trading strategy for **Trantor**, an existing Python automated trading system.

Your responsibility is to translate the strategy and trading principles in this document into explicit, testable, reproducible rules while preserving Trantor's existing architecture and safety controls.

Do not replace this strategy with a different trading strategy because another strategy is easier to implement or backtest.

Do not immediately code the entire system. First inspect the current repository, compare its existing capabilities with this specification, identify gaps and ambiguities, and propose a phased implementation plan.

After presenting your findings, stop and wait for approval before implementing the next phase.

The ultimate objective is a disciplined automated gold trader that:

* Understands the broader market direction.
* Waits for high-quality setups.
* Combines multiple forms of confirmation.
* Defines risk before entering a trade.
* Avoids unnecessary trading.
* Respects daily loss limits.
* Records and explains its decisions.
* Evaluates its performance over hundreds of trades rather than judging itself by a single result.

The bot must be designed to follow its rules consistently, including when the correct decision is to do nothing.

---

# 2. Trading Instrument and Scope

The intended instrument is **`XAUUSD-P`, the gold instrument provided by FPG through the user's MT5 account**.

Treat `XAUUSD-P` as the user's designated gold trading instrument. Do not repeatedly challenge the user's identification of the instrument or substitute an unrelated instrument.

The existing Trantor research has also investigated GC and MGC gold futures. Preserve that existing research and its validation rules.

However, instrument identification and contract economics are separate concerns. Before implementing monetary position sizing, stop-loss risk calculations, or broker execution, inspect and use the actual specifications applicable to the selected instrument.

Verify and document the relevant metadata, including:

* Contract size.
* Tick size and tick value.
* Minimum and maximum order volume.
* Volume increment.
* Account currency and applicable currency conversion.
* Margin requirements.
* Commission and other trading costs.
* Financing or swap costs, where applicable.
* Trading sessions and broker execution constraints.

Do not assume that every gold instrument uses identical contract economics.

This verification is necessary to calculate risk correctly, not to dispute the instrument the user has selected.

Keep the initial strategy focused on gold. Do not expand it to unrelated instruments without explicit approval.

---

# 3. Core Trading Philosophy

The strategy combines three major components.

### A. Higher-timeframe trend following

Use the Daily timeframe to establish the broad market bias and the H4 timeframe to confirm directional structure.

The default strategy should trade in the direction supported by the higher timeframes rather than routinely attempting to predict market reversals.

### B. Lower-timeframe pullback entries

Use M15 to identify precise entry opportunities when price retraces into a meaningful H4 trading zone and produces a qualifying reversal signal.

Do not enter simply because price has reached a support level or an indicator has become overbought or oversold.

### C. Confluence-based decision-making

Evaluate multiple conditions before allowing a trade.

The system must explain why a setup qualifies, which conditions are satisfied, which are missing, and why a trade is rejected.

The objective is to take selective, high-quality setups with clearly defined risk and reasonable reward potential.

The bot must never force a trade merely because it has been running for several hours without finding one.

**Protect capital first. Pursue profit second. Follow the process regardless of the outcome of the previous trade.**

---

# 4. Multi-Timeframe Market Structure

The strategy uses three timeframes with distinct responsibilities.

| Timeframe | Responsibility                       | Purpose                                                                |
| --------- | ------------------------------------ | ---------------------------------------------------------------------- |
| D1        | Market bias                          | Establish the broader bullish or bearish direction                     |
| H4        | Trend confirmation and trading zones | Confirm structure and identify support, resistance, and pullback areas |
| M15       | Entry execution                      | Confirm the pullback reversal and trigger a potential trade            |

All signals must use valid, completed candles.

The implementation must prevent look-ahead bias. At any decision timestamp, the strategy may use only information that would genuinely have been available at that time.

Do not use an unfinished Daily candle to make a decision that requires a completed Daily candle. Apply the same principle to H4 and M15.

## 4.1 Daily bias

The proposed Daily bias uses:

* EMA 50.
* EMA 200.
* Price relative to the previous completed Daily candle's high and low.

Initial directional definitions:

**Bullish bias**

* EMA 50 is above EMA 200.
* No approved structural invalidation condition is present.
* Previous-day high and low provide additional market context.

**Bearish bias**

* EMA 50 is below EMA 200.
* No approved structural invalidation condition is present.
* Previous-day high and low provide additional market context.

**Neutral or uncertain bias**

* The EMAs are equal within a defined tolerance.
* The available evidence does not produce a reliable classification.
* Required market data is missing, stale, or invalid.

Before implementation, propose a deterministic rule for how the previous day's high and low affect the bias. Distinguish between a mandatory filter, a scoring factor, and contextual information.

Do not silently introduce additional indicators or discretionary rules.

## 4.2 H4 trend filter

H4 trend alignment is a mandatory directional filter in the initial strategy.

A long setup must align with the approved bullish H4 trend definition. A short setup must align with the approved bearish H4 trend definition.

If the H4 trend cannot be established reliably, the default decision is no trade.

The strategy permits considering countertrend trades only at a major Daily support or resistance level. However, that exception must remain disabled until a measurable definition of a major Daily level has been approved and tested.

A vague exception must never bypass the directional filter or risk controls.

---

# 5. H4 Trading Zones

The strategy uses four types of potential trading zones:

1. Support and resistance based on swing highs and swing lows.
2. Fibonacci retracement levels at 0.5 and 0.618.
3. Order blocks.
4. Liquidity pools.

Every zone must be identified using deterministic rules.

## 5.1 Support and resistance

Propose a configurable swing-pivot method.

Specify:

* How swing highs and lows are detected.
* How many completed candles are required to confirm a pivot.
* How nearby price levels are merged into a zone.
* How zone width is calculated.
* How zones are invalidated or considered stale.

A pivot must not become available to the strategy before its confirmation conditions have been satisfied.

## 5.2 Fibonacci retracement

Use the 0.5 and 0.618 retracement levels of a clearly defined, confirmed H4 swing.

Specify:

* How swing start and end points are selected.
* How bullish and bearish retracements are calculated.
* How proximity to a Fibonacci level is measured.
* Whether the levels create separate zones or a combined zone.
* How invalidated swings are handled.

Do not calculate levels using future-known swing points.

## 5.3 Order blocks and liquidity pools

Define these concepts using measurable conditions.

For example, an order-block candidate could require an opposing candle followed by a specified displacement move and confirmed structural break.

A liquidity pool could require multiple confirmed swing highs or lows within a defined price tolerance.

These are candidate definitions, not permission to code subjective assumptions without review.

For each zone type, document the calculation method, confirmation requirements, invalidation rules, and evidence available at the time of the trade.

## 5.4 Avoid duplicate confluence

Support/resistance, Fibonacci levels, order blocks, and liquidity pools can overlap.

Do not automatically count several overlapping descriptions of the same price structure as independent evidence without evaluating whether that double-counts the same underlying information.

Preserve the individual evidence for every setup so that subsequent research can measure which factors contribute useful information.

---

# 6. M15 Entry Logic

The M15 timeframe triggers an entry only after the higher-timeframe conditions have been satisfied.

The intended conditions are:

1. H4 trend alignment.
2. Price reaches an eligible H4 trading zone.
3. RSI(14) confirms the potential reversal.
4. A qualifying reversal candlestick pattern appears.
5. Volume confirmation is present.
6. The confluence score reaches the required threshold.
7. The economic-news filter permits trading.
8. Risk controls permit a new position.
9. The trading session and cooldown rules permit entry.

## 6.1 RSI(14)

The intended thresholds are:

* RSI below 30 for a potential long setup during an uptrend.
* RSI above 70 for a potential short setup during a downtrend.

Before implementation, define whether the RSI condition must occur on the trigger candle or within a configurable number of recent completed M15 candles.

Do not change the stated thresholds without approval.

## 6.2 Reversal candlestick patterns

The initial patterns are:

* Bullish engulfing.
* Bearish engulfing.
* Bullish pin bar.
* Bearish pin bar.

Define each pattern mathematically using OHLC data.

Specify the required candle-body and wick relationships for pin bars and whether engulfing is determined from the previous candle's body or full range.

Use completed M15 candles only.

## 6.3 Volume confirmation

Determine what volume information is available for `XAUUSD-P`.

The existing MT5 provider maps `tick_volume` into the application's observation volume field. Do not misrepresent this as verified exchange-traded volume.

Propose a measurable volume-spike rule, including:

* Lookback period.
* Baseline calculation.
* Required volume threshold.
* Handling of missing or invalid observations.

Do not silently substitute data from another instrument.

---

# 7. Confluence Scoring Engine

Each candidate trade receives a score based on the following factors.

| Factor                                                        | Points |
| ------------------------------------------------------------- | -----: |
| Aligned with the Daily trend                                  |     +2 |
| Price at a qualifying H4 support/resistance or Fibonacci zone |     +2 |
| RSI confirmation                                              |     +1 |
| Valid reversal candlestick pattern                            |     +1 |
| Volume confirmation                                           |     +1 |
| No major economic announcement in the next 60 minutes         |     +1 |
| **Maximum**                                                   |  **8** |

Record each factor separately and explain the resulting score.

## 7.1 Confidence tiers

| Score   | Classification    | Maximum risk per trade |
| ------- | ----------------- | ---------------------: |
| 7–8     | High confidence   | 1.0% of account equity |
| 5–6     | Medium confidence | 0.5% of account equity |
| Below 5 | No trade          |                     0% |

These are maximum permitted risk levels, not guaranteed outcomes.

A score of 7/8 does not mean the trade has an 87.5% probability of winning. The score represents the number of defined conditions satisfied, not a calibrated statistical probability.

The initial implementation must use the following interpretation:

* H4 trend alignment is mandatory.
* Daily trend alignment contributes two points.
* A trade against the H4 direction is rejected under the default strategy.
* The countertrend exception remains disabled until separately approved.
* Any missing evidence must be handled according to an explicit rule. Missing information must not automatically be treated as confirmation.

## 7.2 News scoring and safety

The score includes one point when no major announcement is scheduled within the next 60 minutes.

Separately, the execution rules require a 30-minute pause before and after qualifying high-impact announcements.

The scoring condition and the safety filter must remain distinct.

Do not award the news point if the economic calendar is unavailable, stale, or incomplete.

Propose a fail-safe policy for calendar outages before implementation.

---

# 8. Entry, Stop Loss, and Take Profit

## 8.1 Entry

The intended process is:

1. Establish Daily bias.
2. Confirm H4 trend.
3. Identify an eligible H4 zone.
4. Wait for an M15 pullback and qualifying reversal.
5. Calculate the confluence score.
6. Apply session, news, cooldown, and risk filters.
7. Generate a trade candidate only if every mandatory condition passes.

For historical simulation, model execution at the next available executable price after the signal rather than assuming a fill at a price already known when the signal was generated.

Do not implement broker execution during the initial research phases.

## 8.2 Stop loss

Place the stop beyond the relevant support/resistance zone or structural invalidation point, with an appropriate buffer.

Propose a deterministic method that defines:

* The structural reference.
* The buffer.
* Valid price increments.
* Maximum permitted stop distance, if any.
* How a stop that makes the trade unsuitable for the permitted risk is handled.

Never place a stop inside a zone merely to reduce position size.

If no defensible stop can be defined, reject the trade.

## 8.3 Take-profit structure

The intended exit structure is:

* TP1: Close 50% of the original position.
* TP2: Close the remaining 50%.
* After TP1 is confirmed as executed, move the stop on the remaining position to breakeven.

The example prices in the original strategy are illustrative and must never be hardcoded.

Before implementation, propose a deterministic TP1 and TP2 calculation method. Evaluate whether risk multiples, structural targets, or a defined combination best fits the strategy.

The preferred reward profile is approximately 2–3 times the initial risk when the market structure provides a reasonable opportunity. This is a target for setup evaluation, not permission to force distant targets or ignore nearby resistance.

Define how the system handles:

* Partial fills.
* Minimum permitted volume.
* Breakeven after estimated trading costs.
* Gaps through stops or targets.
* Candles that touch both stop and target.
* Stop adjustments after confirmed partial exits.

When historical data cannot determine the sequence of events, use conservative execution assumptions.

---

# 9. Professional Risk Management

Risk management must operate independently of the signal-generation logic.

## 9.1 Risk per trade

The initial maximum risk is:

* High confidence: 1% of account equity.
* Medium confidence: 0.5% of account equity.
* Below threshold: no trade.

The system must estimate the monetary loss at the stop using the actual instrument's contract specifications, tick value, valid volume constraints, account currency, and applicable trading costs.

The conceptual formula

`position_size = (account_balance * risk_percent) / (entry - stop_loss)`

is incomplete for general trading use because the price difference alone does not establish monetary risk.

Use verified instrument economics and round volume down to a valid permitted increment.

Never round upward if that would exceed the risk limit.

If risk cannot be calculated reliably, reject the trade.

Actual losses may exceed the estimated risk because of slippage, gaps, spread changes, or other execution effects.

## 9.2 Daily loss limit

The hard daily loss limit is **-3%**.

When the defined daily loss threshold is reached:

* Stop opening new positions.
* Record the reason for the lockout.
* Continue monitoring existing positions according to their approved risk-management rules.
* Do not resume trading merely because a new signal appears.
* Reset the daily lockout only at the approved trading-day boundary.

Before implementation, define how the loss is measured.

The recommended approach is to compare current equity with equity at the start of the configured trading day and include realized P&L, unrealized P&L, and known trading costs.

The precise day boundary must account for the instrument's applicable trading schedule.

Do not forcibly close existing positions solely because the daily entry limit has been reached unless a separately approved emergency policy requires it.

## 9.3 Daily trade frequency

The desired daily activity is **1–3 quality trades maximum**, not continuous trading.

Implement a configurable daily entry limit with an initial maximum of three entries.

Count actual strategy entries consistently, including entries that are later closed at breakeven or at a loss.

Define how partial exits and position-management actions affect the count. They must not be incorrectly counted as new trades.

The target is selective trading, not a quota.

If no setup qualifies, the correct number of trades is zero.

Do not force three trades merely because the daily maximum is three.

## 9.4 Maximum concurrent positions

Maximum concurrent strategy positions: two.

Track partial exits and remaining exposure as part of the original position.

Prevent duplicate entries caused by repeated polling, restarts, reconnects, or repeated signals.

Do not interpret multiple orders used to manage one trade as multiple independent opportunities.

## 9.5 Cooldown after losses

Implement a configurable cooldown after losses.

Before coding, specify:

* Whether the cooldown follows every losing trade or a sequence of losses.
* The cooldown duration in bars or time.
* Whether it is instrument-specific.
* How it interacts with the daily loss limit.
* How its state survives a restart.

Do not invent an arbitrary duration without documenting and testing it.

## 9.6 Prohibited behaviors

The bot must not:

* Increase position size to recover a previous loss.
* Use martingale.
* Average down without an explicitly approved strategy.
* Pyramid positions without approval.
* Open trades with unverified risk calculations.
* Bypass the daily loss lockout.
* Re-enter repeatedly because the bot is attempting to reach a daily profit target.

---

# 10. Daily Trading Intentions and Decision Discipline

The bot must embody the operating principles of a disciplined professional trader.

These principles must be reflected in explicit software behavior, not merely displayed as motivational messages.

## 10.1 Primary intention: protect capital first

The system's first priority is to preserve capital by following its approved rules.

Its objective is not to generate a profit every day. Its objective is to take only qualifying setups and manage risk consistently.

A properly executed losing trade is not automatically a strategy failure. A profitable trade taken by violating the rules is not evidence that the violation was acceptable.

The system must evaluate the quality of its decisions separately from their financial outcomes.

## 10.2 Daily goals

The intended daily operating targets are:

| Intention                     | Target                               |
| ----------------------------- | ------------------------------------ |
| Quality trades                | 1–3 maximum                          |
| Daily return aspiration       | 0.5%–2% on favorable days            |
| Acceptable outcome            | Flat, no trade, or a controlled loss |
| Hard daily loss limit         | -3%                                  |
| Primary measure of discipline | Compliance with the strategy rules   |

The daily return range is an aspiration, not a guaranteed result or a mandatory quota.

**Never increase risk, lower the entry threshold, relax the news filter, or create additional trades to reach a daily return target.**

If market conditions do not produce a qualifying setup, the system must remain inactive.

A day with no trades can be a successful day if the strategy correctly determines that no opportunity qualifies.

## 10.3 Pre-market assessment

Before the preferred trading session, the system should produce a pre-market assessment.

The assessment should include:

1. Current Daily bias: bullish, bearish, neutral, or unavailable.
2. Current H4 trend and its supporting evidence.
3. Important H4 support and resistance zones.
4. Relevant Fibonacci levels.
5. Relevant order blocks and liquidity pools, if those features have been implemented and validated.
6. Upcoming high-impact economic announcements.
7. DXY direction and US 10-year Treasury yield direction, if reliable data sources are available.
8. The preferred trading session and whether it is currently active.
9. Conditions that must occur before a long setup can qualify.
10. Conditions that must occur before a short setup can qualify.
11. Reasons trading should be avoided.
12. Any missing, stale, or unreliable data.

The assessment must not pretend that DXY, Treasury yields, or economic-calendar data are available when they have not been connected and validated.

### Macro context

Gold can respond to US dollar strength, real yields, interest-rate expectations, liquidity, and risk sentiment.

DXY and US 10-year Treasury yields may therefore provide useful context.

However, the current strategy does not assign these variables a numerical confluence score.

Before adding them as entry filters or score factors, propose a separate research plan explaining:

* Which data source would be used.
* Whether historical data is available at the required timestamps.
* How the relationship would be measured.
* How delayed or missing observations would be handled.
* Whether the added features improve out-of-sample results after costs.

Do not add macro filters to the active strategy merely because the economic rationale sounds convincing. Their value must be tested.

## 10.4 During-session behavior

During the London and New York overlap, the bot should:

* Wait for price to reach a qualifying zone.
* Require the approved entry conditions.
* Require a score of at least five to consider a setup.
* Apply the stricter high-confidence preference described below.
* Enforce all session, news, cooldown, trade-count, concurrent-position, and risk controls.
* Avoid duplicate entries.
* Record why a trade was taken or rejected.

The bot should not chase a move after the qualifying entry conditions have disappeared.

If the price has already moved too far from the intended entry zone and the predefined entry conditions no longer hold, reject the late entry.

## 10.5 After-session review

Produce a daily journal summarizing:

* Trades taken.
* Setups rejected.
* Entry rationale.
* Daily and H4 bias at entry.
* H4 zone involved.
* RSI, candle pattern, volume, and news score components.
* Entry, stop, TP1, and TP2.
* Initial estimated risk.
* Actual result in currency and R-multiple, where available.
* Trading costs.
* Whether every rule was followed.
* Any operational errors or missing data.
* Daily drawdown and remaining risk limits.
* Whether the bot correctly remained inactive when no setup qualified.

Separate **process quality** from **trade outcome**.

A losing trade that followed every rule should be recorded differently from a winning trade that violated the strategy.

Do not change strategy parameters automatically because of one losing trade or one profitable day.

## 10.6 Emotional discipline translated into software

The bot has no emotions to control, but its behavior can still become equivalent to undisciplined trading if its logic is poorly designed.

Explicitly prevent:

* Revenge trading after a loss.
* Increasing risk after a loss.
* Chasing a large candle or a rapidly moving market.
* Re-entering immediately after a stopped-out trade without a new qualifying setup.
* Repeated entries at the same level without a valid new signal.
* Lowering the confidence threshold because no trades have occurred.
* Extending trading hours solely to recover the day's losses.
* Continuing to open positions after the daily loss limit is reached.
* Tuning parameters in response to a small number of recent outcomes.

The system should remain suspicious of unusually large price movements until the approved rules establish whether they present a valid setup.

Its operating principle is:

**Show up, respect the plan, take only what qualifies, protect capital, and let the edge play out over time.**

---

# 11. Trading Sessions and Economic News

## 11.1 Preferred session

The preferred trading period is the London and New York overlap.

Verify the instrument's actual trading hours, broker server timezone, session definitions, daylight-saving changes, and applicable holidays before implementation.

Do not hardcode a UTC session window that becomes incorrect when daylight-saving rules change.

The bot should avoid entering trades outside its approved trading window unless a separately approved rule allows it.

## 11.2 High-impact news

The initial news-avoidance rule is to pause new entries:

* 30 minutes before a qualifying high-impact announcement.
* 30 minutes after the announcement.

Important events include:

* US Nonfarm Payrolls.
* Federal Open Market Committee decisions and relevant press conferences.
* US Consumer Price Index releases.

The scoring engine separately awards one point if there is no major economic announcement in the next 60 minutes.

Specify:

* The calendar source.
* Relevant currencies and event categories.
* Event-impact classification.
* Timezone handling.
* Daylight-saving behavior.
* Schedule changes.
* Missing or stale data behavior.
* Interaction between the 30-minute entry restriction and the 60-minute score condition.

If the calendar is unavailable, the bot must not claim that the news filter has passed.

Define a fail-safe policy before implementation.

---

# 12. Historical Data and Backtesting

The strategy must be tested using reliable historical data before demo forward-testing.

A successful test suite proves that code behaves according to its tests. It does not prove that the strategy is profitable.

## 12.1 Required timeframes

The research dataset must support:

* D1.
* H4.
* M15.

Candles must be synchronized and aligned so that each decision uses only information available at that time.

Record the instrument, broker, symbol, timeframe, data range, acquisition time, and applicable metadata.

## 12.2 Historical coverage

Target at least two years of usable historical data, preferably more where reliable history is available.

Report the actual data range and candle count for each timeframe.

Do not fabricate missing candles or silently substitute another instrument.

If the required history cannot be obtained reliably, report the limitation and stop the affected research phase.

Two years is a minimum research target, not proof of statistical reliability.

## 12.3 Realistic costs and fills

Account for:

* Spread.
* Commission.
* Slippage.
* Swap or financing costs, where applicable.
* Contract specifications.
* Position-size constraints.
* Price gaps.
* Partial exits.
* Stop and target execution.
* Trading sessions.
* News restrictions, where historical event data is available.

If costs are unknown, identify them as unknown and use clearly labelled sensitivity scenarios.

A cost-free backtest may be used for diagnostics but must never be presented as the expected real-world result.

Use conservative assumptions when historical candles cannot establish which intrabar event occurred first.

## 12.4 Required performance reporting

Report:

* Total return.
* Annualized return, where meaningful.
* Maximum drawdown.
* Number of trades.
* Win rate.
* Average winning trade.
* Average losing trade.
* Average R-multiple.
* Expectancy in R.
* Profit factor.
* Gross profit and loss.
* Trading costs.
* Exposure and holding duration.
* Results by year.
* Results by confidence tier.
* Results by long and short direction.
* Results before and after costs.
* Daily trade-count distribution.
* Number of days with no trades.
* Number of days reaching the daily loss limit.
* Number of rejected setups and their rejection reasons.
* Rule-compliance statistics.

Distinguish simulated results from actual account performance.

## 12.5 Out-of-sample evaluation

Use chronological training, validation, and out-of-sample periods.

Never randomly shuffle time-series data.

Protect the final holdout period from repeated parameter tuning.

Use anchored walk-forward evaluation where practical.

Report insufficient samples honestly. If the available data does not support a meaningful conclusion, mark the result inconclusive.

Do not assume that a 40–55% win rate is guaranteed or required. Evaluate expectancy after costs, realized reward-to-risk, drawdown, and robustness across market conditions.

## 12.6 No automatic parameter changes

Do not automatically retune the strategy after each loss or each day.

If periodic tuning is later approved, it must follow a documented research process that includes:

* A predefined review schedule.
* A defined training window.
* A protected out-of-sample evaluation.
* Versioned parameter sets.
* Comparison against the previous approved version.
* A rollback mechanism.

The bot must not change its own rules simply because recent performance is poor.

---

# 13. Trantor Architecture and Safety Controls

The project is located at:

`C:\xampp\htdocs\Trantor`

Inspect:

* Configuration and typed models.
* Market-data interfaces and providers.
* MarketDataService validation.
* Existing strategy protocol.
* Gold analysis and replay components.
* Existing safety controls.
* Broker adapters.
* Test suite and fixtures.
* MT5 read-only probe and provider.
* Documentation and implementation status.

Preserve the current architecture wherever practical.

The existing project uses `AssetClass.UNVERIFIED_GOLD` for the research path associated with `XAUUSD-P`, while existing GC/MGC-specific analysis remains separate.

Do not force `XAUUSD-P` through GC/MGC-specific validation simply to reuse an existing type.

If the research strategy needs a separate path, implement the smallest coherent extension.

Avoid:

* Unnecessary rewrites.
* Deleting existing functionality.
* Duplicate interfaces.
* Expanding to unrelated instruments.
* Replacing this strategy with Donchian breakout.
* Weakening safety checks to make integration easier.

## Safety state

The system has remained paper-only, with live trading disabled and a safe non-executing broker adapter as the default.

Preserve these controls.

The existing MT5 provider is intended for read-only market-data access. Do not add MT5 order submission or enable trading during research implementation.

Do not introduce account credentials or real-money execution.

Do not change trading mode, broker adapter, live-trading flags, or execution permissions.

The connected MT5 account is a demo account. This does not authorize the bot to place orders.

---

# 14. Implementation Phases

Work in small, independently testable phases. Do not implement all phases in one operation.

## Phase A: Repository and strategy feasibility audit

Inspect the repository and compare its actual state with this specification.

Report:

1. Current architecture.
2. Reusable components.
3. Missing components.
4. Ambiguous rules requiring decisions.
5. Data and instrument metadata gaps.
6. D1, H4, and M15 history availability.
7. Missing cost, session, volume, and news data.
8. Proposed implementation sequence.
9. Exact files likely to change in the next phase.
10. Tests required for each phase.
11. Whether the pre-market macro assessment can be implemented immediately or requires separate data integrations.
12. A GO/NO-GO recommendation for historical data acquisition.

Do not modify code during this phase.

Present the report and stop for approval.

## Phase B: Historical data acquisition and audit

After approval, implement only the data acquisition and validation work needed for D1, H4, and M15.

Use bounded date chunks, bounded retries, explicit failure reporting, and reproducible saved datasets.

Validate:

* Candle ordering.
* Duplicate timestamps.
* OHLC consistency.
* Completed-candle status.
* Timezone normalization.
* Missing intervals and gaps.
* Chunk boundaries.
* Repeatability.
* Changes between repeated retrievals.
* Volume-field meaning.
* Available spread and cost metadata.

Do not silently classify gaps as weekends or holidays without adequate trading-calendar evidence.

Do not implement the trading strategy or backtest engine during this phase.

Produce an audit report and stop for review.

## Phase C: Finalize deterministic strategy rules

After approval, produce a versioned strategy specification covering:

* Daily bias.
* H4 trend filter.
* Swing and zone detection.
* Fibonacci calculation.
* Order blocks and liquidity pools.
* M15 entry triggers.
* Volume confirmation.
* Confluence scoring.
* Stop placement.
* TP1 and TP2.
* Position sizing.
* Daily loss controls.
* Daily entry limit.
* Concurrent-position limit.
* Cooldown.
* News filter.
* Trading sessions.
* Daily pre-market assessment.
* Macro context.
* Trade journal and rule-compliance evaluation.
* Conservative execution assumptions.

Every rule must be deterministic and testable.

Identify which features can be implemented immediately and which require additional data or separate research.

Present the specification and stop for approval.

## Phase D: Research strategy implementation

After approval, implement the research strategy using the existing interfaces and approved rules.

Requirements:

* Deterministic output.
* Completed candles only.
* No future data leakage.
* Explicit rejection reasons.
* Full confluence breakdown.
* Clear trade direction and entry rationale.
* Proposed entry, stop, and target levels.
* Risk estimates only when instrument economics are verified.
* Daily trade-limit enforcement in the research model.
* Daily loss-lockout simulation.
* Cooldown and duplicate-signal handling.
* No broker execution.

Write unit tests for each rule, edge cases, missing data, conflicting conditions, and risk-control behavior.

Do not proceed directly to order execution.

## Phase E: Historical simulation

After the strategy implementation is reviewed, implement or extend the backtest engine.

Include:

* Realistic order timing.
* Conservative intrabar handling.
* Trading costs and sensitivity analysis.
* Partial-exit simulation.
* Position-state tracking.
* Daily risk controls.
* Daily entry limits.
* Cooldown.
* Chronological evaluation.
* Out-of-sample testing.
* Reproducible results.
* Detailed trade logs and performance reports.
* Process-compliance statistics.

Test for look-ahead bias, duplicate trades, impossible fills, invalid position sizing, and accounting errors.

Present the results and limitations without claiming profitability based solely on a successful test suite or an attractive backtest.

## Phase F: Demo forward-testing readiness

Only after the research and backtest phases have been reviewed should a separate proposal be prepared for demo forward-testing.

The proposal must specify:

* Signal generation.
* Broker execution requirements.
* Order and position reconciliation.
* Duplicate-order prevention.
* Stop and target management.
* Partial-fill handling.
* Restart recovery.
* Logging and monitoring.
* Telegram alerts, if approved.
* Emergency-stop behavior.
* Maximum-risk enforcement.
* Daily loss lockout.
* Daily trade limits.
* Operational failure handling.

Do not implement execution until separately authorized.

Live trading remains outside the scope of this task.

---

# 15. Engineering Standards

All implementation work must:

* Follow existing project conventions.
* Use typed models where appropriate.
* Have deterministic tests.
* Handle missing and invalid data explicitly.
* Log meaningful errors and rejection reasons.
* Avoid silent fallback behavior.
* Avoid network calls in unit tests unless explicitly designed as integration tests.
* Keep historical data fixtures separate from production code.
* Avoid committing large generated datasets unless project policy requires it.
* Document assumptions and limitations.
* Preserve backward compatibility where practical.
* Run the complete existing test suite after changes.
* Report actual test counts and results.
* List all changed and created files.
* Run syntax/compile checks and `git diff --check` where applicable.

Never report a test as passed if it was not actually run.

Never claim that a feature, data source, or contract capability has been verified when it has not.

---

# 16. Immediate Instruction

**Begin with Phase A only.**

Inspect the current Trantor repository and compare its actual state with this strategy specification.

Do not implement the strategy yet.

Do not change code, settings, trading permissions, broker configuration, or safety controls.

Return a technically complete feasibility report containing:

1. Current architecture and reusable components.
2. D1/H4/M15 data availability.
3. Current `XAUUSD-P` metadata and any remaining contract-economic details needed for accurate risk calculations.
4. Strategy rules that are already precise.
5. Strategy rules requiring deterministic definitions.
6. Missing cost, session, volume, and news data.
7. Whether DXY and US 10-year Treasury yield data are available.
8. A proposed phased implementation plan.
9. Exact files likely to change in the next phase.
10. Testing requirements.
11. A GO/NO-GO recommendation for historical data acquisition.

Preserve the intended multi-timeframe gold strategy and professional trading discipline described here.

Do not replace the strategy with the earlier Donchian breakout proposal.

Do not begin coding until the Phase A findings have been presented and approved.

Stop after delivering the report and wait for approval.
