# Research Decision Record Contract

- **Schema name:** `trantor.research_decision`
- **Schema version:** `1.0`
- **Status:** Implemented and corrected after the research-record integrity audit; research-only.
- **Backtester:** Not implemented. This contract defines how a future backtester must persist `StrategyDecision` records.

---

## 1. Purpose

The canonical record preserves the authoritative strategy decision without
turning a `TradeSignal` or `XAUUSDPSignalCandidate` into the sole source of
truth. It is designed for later audit and replay of research decisions, not for
broker execution.

The serializer/validator is implemented in:

```text
src/trantor/strategy/research_record.py
```

The authoritative input is always:

```text
StrategyDecision
```

A bare `TradeSignal` or `XAUUSDPSignalCandidate` is rejected by the serializer.

---

## 2. Identity

Every record carries:

```text
strategy_name
strategy_version
variant_id
variant_name
research_only
```

Allowed current identities:

| Variant ID | Strategy name | Version | Variant name |
|---|---|---|---|
| `xauusd_p_approved_v1` | `xauusd_p_multi_timeframe` | `1.0.0` | `approved` |
| `xauusd_p_filter_isolated_v1` | `xauusd_p_multi_timeframe_research_filter_isolated` | `1.0.0` | `research_filter_isolated` |

The serializer rejects a decision whose `variant_id` does not match the supplied
`StrategyIdentity`. The validator rejects missing, empty, unknown, or
inconsistent identity fields. Filter mode is identity-bound: the approved
variant must record `approved`, and the isolated research variant must record
`research_isolated`.

`StrategyDecision` carries `variant_id` but does not itself carry the strategy
name/version. The serializer therefore requires a declared `StrategyIdentity`;
it does not fabricate these values from the decision.

Ordinary attribute assignment is guarded at the instance level:

- `name`
- `version`
- `variant_id`
- `variant_name` via `identity.variant_name`
- `identity`
- the backing `_identity`

Attempting to assign these on an instance raises `AttributeError`. This is an
accidental-modification safeguard, not absolute tamper-proofing: direct
`__dict__` access or `object.__setattr__` can bypass it, and a frozen dataclass
can likewise be changed through object-model bypasses. The contract does not
claim that identity matching establishes the historical origin of a decision.
There is no signature, producer token, or object-identity provenance.

---

## 3. Time Semantics

Every record contains:

```text
decision_time
decision_time_original
time_semantics = dataset_time_unverified_absolute_utc
absolute_utc_provenance = unverified
```

`decision_time` is ISO-8601 with its represented timezone offset. The serializer
does not convert it to UTC and does not claim verified absolute UTC provenance.
`decision_time_original` preserves the caller-supplied original representation;
when none is supplied, it is the ISO representation of the decision timestamp.

`StrategyDecision` does not contain a raw source-time string. If an upstream
adapter has one, it must pass it as `original_decision_time`; otherwise the
canonical ISO representation is the only available original representation.
The record does not claim that this resolves the broker/MT5 UTC provenance gap.

The same limitation applies to nested context timestamps. The record does not
claim that session windows, split boundaries, or news timestamps are verified
absolute UTC.

---

## 4. Top-Level Schema

Required top-level fields:

```text
schema
schema_version
record_type
authoritative_source
strategy_name
strategy_version
variant_id
variant_name
research_only
execution_ready
decision_time
decision_time_original
time_semantics
absolute_utc_provenance
symbol
timeframe
instrument
status
rejections
score
session
news
news_unaware
plan
candidate
daily_bias
h4_trend
zone
risk
limitations
```

`record_type` is `strategy_decision`; `authoritative_source` is
`StrategyDecision`. `symbol` must be `XAUUSD-P`; `timeframe` is the research
decision timeframe `15m`. `StrategyDecision` does not carry a separate timeframe
field, so the record uses the strategy's documented M15 decision timeframe.
`execution_ready` must be false; `research_only` must be true.

---

## 5. Decision Content

### Outcome and rejections

`status` is the enum value (`candidate` or `rejected`). `rejections` is a list of:

```text
code
message
```

Every rejection is retained, including early data, session, news, risk, and
target-plan rejections.

### Score and confidence

The `score` object contains:

```text
total
maximum
confidence
factors[]
```

Each factor contains `name`, `points`, `awarded`, `available`, and `detail`.

### Session

The `session` object contains:

```text
in_overlap
calendar_verified
research_allowed
reason
filter_mode
```

`filter_mode` is `approved` or `research_isolated`. It is identity-bound:
the approved identity requires `approved`; the isolated research variant
requires `research_isolated`. For schema version `1.0`, `calendar_verified` must
be `false` because no verified session/holiday calendar evidence is available.
A `true` value is rejected rather than treated as proof.

### News

The `news` object contains:

```text
available
safe
point_awarded
unaware
reason
```

The top-level `news_unaware` field records the decision's news-unaware state.
For schema version `1.0`, every accepted record must be news-unaware:
`news_unaware=true`, `news.available=false`, `news.safe=false`, and
`news.point_awarded=false`. The research variant is explicitly required to
remain news-unaware and cannot award the news point. An approved news-aware
decision is not serializable under this version because the record carries no
verified-economic-calendar evidence. This is a fail-closed limitation, not a
claim that the approved strategy cannot evaluate news-aware inputs.

### Trade plan

When a candidate exists, `plan` contains:

```text
entry
stop
tp1
tp2
risk_distance
tp1_fraction
tp2_fraction
breakeven
breakeven_costs_verified
monetary_sizing_status
partial_exit_status
```

When no candidate exists, `plan` is null. For a candidate:

- prices satisfy `stop < entry < TP1 < TP2` for a buy or
  `stop > entry > TP1 > TP2` for a sell;
- `risk_distance` equals the absolute entry-to-stop distance;
- TP1 and TP2 fractions are positive and sum to `1.0`;
- `breakeven` equals the entry reference when present;
- `breakeven_costs_verified` must be `false`;
- `monetary_sizing_status` must remain
  `blocked_unverified_contract_economics`;
- partial-exit volume feasibility remains blocked.

The existing `TradePlan` exposes the breakeven price and verification flag, not
a separate textual breakeven rule; the serializer does not fabricate rule text
that is not present in the decision. The record does not contain a position-size
field, so partial fractions cannot be tested against an actual sized position;
only configuration consistency is validated.

### Candidate consistency

For a candidate record, the candidate `decision_time` must equal the top-level
decision timestamp, `entry_time` cannot precede the decision timestamp,
`entry_reference` must equal plan entry, and candidate confidence must equal the
score confidence. The `StrategyDecision` model has no independent top-level
directional-intent field, so direction is validated through the candidate side
and its plan geometry; the record cannot prove an intent that is not represented.

### Supporting context

`daily_bias`, `h4_trend`, `zone`, `risk`, and `candidate` preserve the available
decision context. Nested datetimes use the same offset-preserving ISO form.
When `risk` is present, `risk.sizing_status` must be
`blocked_unverified_contract_economics` and `risk.cost_adjusted` must be
`false`; favorable sizing or cost-adjusted status is rejected.

### Limitations

`limitations` is a list that records the applicable research limitations.
Required strings in schema version `1.0`:

- `dataset_time_does_not_claim_verified_absolute_utc`
- `session_calendar_unverified`
- `news_calendar_unverified`
- `cost_verification_unavailable`
- `monetary_sizing_blocked`
- `research_only`
- `execution_not_ready`

Session-isolation and news-unaware labels are added where applicable; monetary
sizing and partial-exit blockers are added for a candidate.

---

## 6. Enum, Datetime, Numeric, and Field Serialization

- Enums serialize as their explicit `.value` strings.
- Datetimes serialize as ISO-8601 with an offset.
- No secret, credential, account, broker, order, or monetary-size data is
  included.
- The canonical JSON form uses sorted keys, compact separators, and no
  implementation-dependent defaults.
- All accepted numeric fields must be finite. `NaN`, positive infinity, and
  negative infinity are rejected during serialization, validation, and parsing.
- Price, level, risk, and ratio fields are normalized to binary64 floats in
  canonical output. Therefore `100` and `100.0` canonicalize identically for
  those fields. Integer-valued counts and score points remain integers.
- Unknown fields are rejected at their containing record level. Unsupported
  object types fail with `ResearchRecordSchemaError` rather than being silently
  converted to strings or another representation.
- Canonical serialization failures are translated into
  `ResearchRecordSchemaError` rather than leaking raw `ValueError`/`TypeError`.

`canonical_json()` validates and normalizes the record before serializing it.

---

## 7. Validation and Versioning

The validator rejects:

- unsupported schema name or schema version;
- missing required fields or unsupported/unknown fields;
- non-finite numeric values;
- empty or unknown identity values;
- inconsistent strategy name/version/variant name for a variant ID;
- non-boolean research-only/news fields;
- verified session claims or any news availability/safety/point claim;
- non-ISO or timezone-naive decision times;
- invalid status, session filter mode, or score shape;
- candidate/decision timestamp mismatch, candidate entry before decision, or
  candidate confidence mismatch;
- invalid long/short stop/target geometry, risk-distance mismatch, breakeven
  mismatch, or invalid partial-exit fractions;
- non-boolean or unsupported compliance fields;
- a plan whose monetary-sizing status is not blocked;
- a plan claiming breakeven cost verification;
- missing dataset-time, session-calendar, news-calendar, cost, monetary-sizing,
  research-only, or execution-readiness limitations;
- a record claiming `research_only=false`, `execution_ready=true`,
  `absolute_utc_provenance=verified`, or a favorable risk-sizing/cost status.

Unsupported versions fail with `ResearchRecordSchemaError` rather than being
silently upgraded or guessed.

---

## 8. Round-Trip and Determinism

The canonical test sequence is:

```text
serialize_strategy_decision(decision, identity)
    -> validate_research_record(record)
    -> canonical_json(record)
    -> parse_research_record(json)
    -> canonical_json(parsed)
```

Repeated serialization of the same immutable decision must produce equivalent
canonical JSON. The tests cover approved and research variants, early
rejections, identity mismatches, unsupported versions and fields, missing
required fields, plan preservation, variant mutation attempts, non-finite
numbers, compliance/cost claims, timestamp/direction/geometry consistency,
numeric canonicalization, and rejection of candidate/`TradeSignal`-only records.

`parse_research_record()` validates and returns the record mapping; it
deliberately does not reconstruct a mutable `StrategyDecision` object. This
keeps the serialized record as the audit artifact and avoids silently rebuilding
decision state.

---

## 9. Backtester Boundary

The future Phase E backtester must:

1. consume `StrategyDecision` as the authoritative strategy output;
2. serialize decisions through this contract;
3. include the returned `StrategyIdentity`;
4. treat `TradeSignal` and candidate-only objects as insufficient;
5. label records as research-only and dataset-time;
6. not infer execution readiness, costs, verified UTC provenance, or monetary
   risk from the record.

No backtester or execution implementation is authorized by this contract.
