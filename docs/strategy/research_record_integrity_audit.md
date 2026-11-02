# Research Record Integrity Audit

- **Date:** 2026-11-02
- **Scope:** Read-only audit of the canonical research-decision serializer, validator, parser, strategy identity, tests, and documentation.
- **Repository:** `C:\xampp\htdocs\Trantor`
- **Phase E status:** Not implemented, not authorized, and not exercised by this audit.
- **Files inspected:** `src/trantor/strategy/research_record.py`, `src/trantor/strategy/xauusd_p.py`, `src/trantor/strategy/xauusd_p_research.py`, `tests/test_xauusd_p_strategy.py`, `docs/strategy/research_record_contract.md`, `docs/strategy/research_variant_session_interface_design.md`, `docs/strategy/phase_e_readiness_review.md`.

---

## 1. Overall Verdict

**FAIL**

The happy-path implementation is real: it serializes approved and research decisions, preserves variant/filter identity, produces deterministic JSON for valid finite records, and all existing tests pass. However, the contract does **not** satisfy the audit criteria as a future authoritative research-backtester input:

- the validator and parser accept non-finite numbers;
- `serialize_strategy_decision()` can return a record containing `NaN`, even though `canonical_json()` later rejects it with a raw `ValueError`;
- records can claim unverified session/news/sizing/cost facts (`calendar_verified`, `news.safe`, `risk.sizing_status`, `breakeven_costs_verified`);
- identity is only structurally checked, not provenance-checked, and the “immutable” identity can be bypassed through Python object interfaces;
- the parser accepts materially inconsistent candidate/plan timing and direction fields.

The failures are record-integrity and research-safety failures. No trading or backtest failure was observed.

---

## 2. Findings Ranked by Severity

### High — H1: Non-finite numbers are accepted by validation and parsing

**Finding:** `validate_research_record()` accepts `NaN`/infinite values in numeric fields because `_require_number()` and `_optional_number()` check only `isinstance(value, (int, float))` and, for required plan fields, `value <= 0`. They do not use `math.isfinite()`. `parse_research_record()` uses `json.loads()` with its default permissive handling of `NaN` and `Infinity`, then runs the same incomplete validation.

**Evidence:**

- `src/trantor/strategy/research_record.py:405-429` — numeric helpers lack finite checks.
- `src/trantor/strategy/research_record.py:748-761` — parsing delegates to `json.loads()` and `validate_research_record()`.
- `src/trantor/strategy/research_record.py:766-773` — `allow_nan=False` protects only the final canonical JSON dump, after validation has already accepted the record.
- `src/trantor/strategy/xauusd_p.py:92-98` already demonstrates the correct pattern for strategy values (`math.isfinite()`), but the record validator does not reuse equivalent checks.

**Read-only probe results:**

```text
serialize risk_fraction=NaN
  serialize_risk_nan_ACCEPTED nan
  canonical_risk_nan ValueError Out of range float values are not JSON compliant: nan

parse JSON containing "breakeven": NaN
  parse_nan_ACCEPTED nan
```

The failure is not limited to manually edited record dictionaries. A `StrategyDecision` can be built with a `RiskDecision` containing `NaN`; `serialize_strategy_decision()` accepts it and returns a record. `canonical_json()` then fails with a raw `ValueError`, not a `ResearchRecordError`.

**Impact:** A future consumer that uses the returned mapping, or `parse_research_record()` output, rather than always calling `canonical_json()`, can consume non-finite values. This directly violates the audit requirement that non-finite numbers be rejected.

---

### High — H2: Records can claim unverified compliance, sizing, or cost facts

**Finding:** The validator constrains the research variant’s news fields and the identity/filter-mode combination, but it does not constrain several fields that can be interpreted as verified compliance or cost claims.

**Evidence:**

- Session: `src/trantor/strategy/research_record.py:588-604` requires booleans and binds filter mode to the variant, but accepts `session.calendar_verified=true` for the approved identity. There is no independent evidence in the record that a verified calendar exists. The repository documents the absence of a verified session/holiday calendar (`docs/strategy/phase_e_readiness_review.md:292-293`).
- News: `src/trantor/strategy/research_record.py:606-621` constrains research-isolated records, but for the approved identity it accepts `news.available=true`, `news.safe=true`, `news.unaware=false`, and even `news.point_awarded=true` without a verified economic calendar. The readiness review states that no verified historical economic calendar exists (`docs/strategy/phase_e_readiness_review.md:293`).
- Risk sizing: `src/trantor/strategy/research_record.py:730-737` checks only that `risk.sizing_status` is a non-empty string. A serialized record can say `"sized"`.
- Costs: `src/trantor/strategy/research_record.py:646-647` requires `breakeven_costs_verified` to be a boolean but does not require it to be `false`, so a record can claim verified costs.
- Positive controls: `absolute_utc_provenance` is forced to `unverified`, top-level and candidate `execution_ready` are forced to `false`, `research_only` is forced to `true`, and plan `monetary_sizing_status`/`partial_exit_status` are forced to their blocked values (`research_record.py:511-523`, `638-655`, `671-674`). These controls are working.

**Read-only probe results:**

```text
session.calendar_verified = true                    -> ACCEPTED
risk.sizing_status = "sized"                        -> ACCEPTED
plan.breakeven_costs_verified = true                -> ACCEPTED
approved news available/safe=true, unaware=false, point_awarded=true -> ACCEPTED
```

**Impact:** A record can falsely appear session-verified, news-safe, monetarily sized, or cost-verified. Those are exactly the claims the research-only contract is supposed to prevent. The contract needs explicit invariants or a separate trusted-verification object; merely recording caller-provided booleans is not sufficient.

---

### Medium — M1: The serializer checks structural consistency, not strategy provenance

**Finding:** `serialize_strategy_decision()` checks that `decision.variant_id` matches the supplied identity and that the identity’s name/version/variant name match a static mapping. It cannot establish that the identity object came from the strategy that produced the decision. There is no producer token, signature, object reference, or immutable decision-level strategy identity.

**Evidence:**

- `src/trantor/strategy/research_record.py:253-284` — supplied identity checks.
- `src/trantor/strategy/research_record.py:44-61` — static `_ALLOWED_IDENTITIES` and `_ALLOWED_IDENTITY_FILTER_MODES`.
- `src/trantor/strategy/xauusd_p.py:625-642` — `StrategyDecision` stores only `variant_id`; it does not store the producing strategy instance or strategy name/version.
- A caller can construct a valid `StrategyIdentity` with the exact approved or research constants and pass it with a matching decision. That is accepted because it is structurally valid, even though no producer link exists.

**Verdict:** Different-variant substitution is blocked for ordinary paths; same-variant provenance cannot be established. This is an application-level integrity guarantee, not cryptographic or object-identity provenance.

---

### Medium — M2: Identity immutability is bypassable through normal Python object interfaces

**Finding:** The new identity protection blocks ordinary attribute assignment, but it does not provide absolute immutability. `StrategyIdentity` is a frozen dataclass, and the strategy’s `__setattr__` blocks public identity names, but both can be bypassed with `__dict__` or `object.__setattr__`.

**Evidence:**

- `src/trantor/strategy/xauusd_p.py:101-122` — `StrategyIdentity` is `frozen=True, slots=True`.
- `src/trantor/strategy/xauusd_p.py:1546-1572` — read-only properties and `__setattr__` guard.
- Dynamic probe:

```text
variant._identity through variant.__dict__:
  before xauusd_p_filter_isolated_v1
  after_dict xauusd_p_approved_v1

object.__setattr__(variant, "_identity", ...):
  after_object xauusd_p_approved_v1

object.__setattr__(strategy_identity, "variant_id", ...):
  before xauusd_p_approved_v1
  after xauusd_p_filter_isolated_v1
```

- `StrategyDecision` is also frozen (`xauusd_p.py:625`), but `object.__setattr__` can change its `variant_id`.

**Verdict:** The protection is adequate against ordinary `variant.variant_id = ...` assignment, but not against a hostile in-process caller. This must not be described as absolute security.

---

### Medium — M3: Parser accepts materially inconsistent candidate and plan records

**Finding:** The validator checks candidate `entry_reference` against plan `entry` and parses candidate timestamps, but it does not check other material relationships:

- candidate `decision_time` can differ from the top-level `decision_time`;
- candidate `side` can be changed from buy to sell while the plan’s stop/TP ordering remains unchanged;
- plan stop/TP ordering is not checked against side;
- candidate `entry_time` is not checked for a valid relationship to `decision_time`;
- risk/session/news fields can be changed while remaining semantically inconsistent with the rest of the record.

**Evidence:**

- `src/trantor/strategy/research_record.py:657-679` — candidate checks stop at side/time parsing, entry-reference equality, confidence, execution, research mode, and news-unaware consistency.
- `src/trantor/strategy/research_record.py:638-655` — plan numeric checks do not compare stop/TP ordering with side.
- Read-only probes:

```text
candidate.side changed from buy to sell
  side_offset_ACCEPTED

candidate.decision_time changed to another valid timestamp
  candidate_time_mismatch_ACCEPTED
```

**Impact:** A parsed record can be internally plausible to the schema validator but inconsistent with the strategy plan. A backtester could consume a side/stop combination that never came from the strategy.

---

### Low — L1: Numeric type representation is not canonicalized

**Finding:** Integers and floats with the same numeric value serialize differently (`100` versus `100.0`). No numeric normalization is applied. This does not affect repeated serialization of the same object, but two semantically equivalent records can produce different canonical JSON.

**Evidence:**

- `src/trantor/strategy/research_record.py:115-145` — plan values are copied without normalization.
- `src/trantor/strategy/research_record.py:766-773` — JSON serialization preserves the Python source type.
- Read-only probe:

```text
same_semantic_value True
canonical_equal False
```

This is a low-severity determinism limitation, not a reason alone to reject a record. It does mean “equivalent records produce identical canonical JSON” should be stated as “the same record/value types produce identical output.”

---

### Low — L2: Extra fields are accepted by validation and fail only at raw JSON encoding

**Finding:** `_REQUIRED_TOP_LEVEL` guarantees required fields but does not reject unknown fields. An extra field containing an unsupported Python object passes `validate_research_record()` and then fails during `canonical_json()` with a raw `TypeError`.

**Evidence:**

- `src/trantor/strategy/research_record.py:332-365` — required-field set, with no closed set.
- `src/trantor/strategy/research_record.py:766-773` — raw `json.dumps()`.
- Read-only probe:

```text
extra_field_ACCEPTED
extra_field_canonical TypeError Object of type object is not JSON serializable
```

This does not affect serializer output because the serializer does not add arbitrary fields, but it means the parser is not a strict schema boundary and unsupported data can fail later with an implementation error rather than a clear contract error.

---

### Low — L3: Identity tables are duplicated and future variants can raise an uncaught `KeyError`

**Finding:** `_ALLOWED_IDENTITIES` and `_ALLOWED_IDENTITY_FILTER_MODES` are separate hardcoded maps. They are aligned today, but adding a future variant to one map without the other causes `validate_research_record()` to access a missing key and raise `KeyError` rather than a clear `ResearchRecord...Error`.

**Evidence:**

- `src/trantor/strategy/research_record.py:44-61` — two independent maps.
- `src/trantor/strategy/research_record.py:595` — direct indexing by `variant_id`.

This is a maintainability/fail-closed-quality issue, not a current exploit.

---

### Low — L4: Tests do not cover the failing integrity paths

**Finding:** The added tests cover the happy path, schema version, missing fields, identity mismatch, filter-mode mismatch, news-point mutation, early rejections, and determinism. They do not cover non-finite numbers, parser acceptance of `NaN`, identity bypass through `__dict__`/`object.__setattr__`, unconstrained session/news/sizing/cost claims, candidate side/time inconsistency, or unsupported-object handling.

**Evidence:**

- `tests/test_xauusd_p_strategy.py:1324-1331` — determinism/round-trip happy path.
- `tests/test_xauusd_p_strategy.py:1334-1343` — unsupported schema.
- `tests/test_xauusd_p_strategy.py:1356-1379` — inconsistent score/fraction/filter-mode values.
- `tests/test_xauusd_p_strategy.py:1382-1411` — identity mismatch.
- `tests/test_xauusd_p_strategy.py:1414-1421` — candidate/`TradeSignal` rejection.
- `tests/test_xauusd_p_strategy.py:1424-1434` — public mutation attempts.
- `tests/test_xauusd_p_strategy.py:1437-1453` — early rejection identity/reason.
- No record test uses `NaN`, `Infinity`, `__dict__`, `object.__setattr__`, `calendar_verified=true`, `news.safe=true`, `sizing_status="sized"`, or `breakeven_costs_verified=true`.

---

## 3. Audit A — Strategy Identity Provenance

### A.1 Different-variant substitution

**Result: blocked for normal paths.**

`serialize_strategy_decision()` checks:

```text
decision.variant_id != identity.variant_id -> ResearchRecordIdentityError
```

at `src/trantor/strategy/research_record.py:261-264`.

The approved decision with the research identity, and the reverse, are rejected. Tests cover this at `tests/test_xauusd_p_strategy.py:1382-1411`.

### A.2 Decision variant ID

**Result: checked.**

The same check at `research_record.py:261-264` compares the decision’s `variant_id` to the supplied identity. A mismatched identity cannot be used to serialize normally.

### A.3 Strategy name and version

**Result: checked against the declared variant, but not derived from the producing strategy.**

`research_record.py:265-276` compares the supplied identity’s `strategy_name`, `strategy_version`, and `variant_name` with the static entry for `variant_id`. This prevents an identity with the correct variant ID but the wrong name/version/variant name from being accepted. It does not establish that the identity belongs to the object that produced the decision.

### A.4 Public mutation and bypass

**Result: ordinary assignment is blocked; object-model bypass remains possible.**

The strategy properties and `__setattr__` guard are at `xauusd_p.py:1553-1572`. Direct assignment to `variant.variant_id`, `name`, `version`, `identity`, or `_identity` is rejected. However:

- `variant.__dict__["_identity"] = ...` bypasses `__setattr__`;
- `object.__setattr__(variant, "_identity", ...)` bypasses it;
- `object.__setattr__` bypasses the frozen `StrategyIdentity` and frozen `StrategyDecision`;
- class-level `_identity` reassignment remains possible.

This is a Python object-model limitation, not an application-level provenance guarantee. The documentation should not call this absolute immutability.

### A.5 Early rejection identity

**Result: correct for the current two variants, but implemented by a wrapper convention.**

`_reject()` at `xauusd_p.py:1509-1540` does not pass `variant_id`; `StrategyDecision` defaults to `_APPROVED_VARIANT_ID` at `xauusd_p.py:642`. The research wrapper corrects this with `replace(decision, variant_id=self.variant_id)` at `xauusd_p_research.py:109`. The early-rejection tests pass at `tests/test_xauusd_p_strategy.py:1193-1206` and `1437-1453`.

This is correct for the current implementation. A future subclass that returns a decision from `_reject()` without the wrapper replacement would record the approved ID.

### A.6 Approved vs research confusion

**Result: filter-mode confusion is blocked; compliance-field confusion is not.**

`research_record.py:595-604` binds filter mode to the identity:

- approved identity requires `approved`;
- research identity requires `research_isolated`.

`research_record.py:613-621` enforces research-isolated news-unawareness and no news point. Tests cover this at `tests/test_xauusd_p_strategy.py:1158-1175` and `1296-1307`.

However, an approved record can carry `calendar_verified=true`, `news.safe=true`, or `news.available=true` with no independent verification. The identity check therefore prevents filter-label confusion, not false compliance claims.

---

## 4. Audit B — Canonical Serialization

| Check | Result | Evidence |
|---|---|---|
| Repeated serialization of same decision | **Pass** | `tests/test_xauusd_p_strategy.py:1324-1331`; `canonical_json()` is deterministic for the same finite record. |
| Key ordering and whitespace | **Pass** | `research_record.py:766-773`; `sort_keys=True`, compact separators. |
| Datetime representation | **Pass with caveat** | `research_record.py:64-68` preserves ISO offset; `time_semantics` and `absolute_utc_provenance` force the dataset-time limitation. |
| Enum representation | **Pass** | `research_record.py:71-72` uses `.value`; tests assert status/filter/confidence strings. |
| Non-finite numbers rejected | **Fail** | `research_record.py:405-429`; `parse_research_record()` accepts `NaN`; `canonical_json()` raises raw `ValueError`. |
| Unsupported objects fail clearly | **Partial** | Extra fields are accepted by validation (`research_record.py:332-365`); `canonical_json()` raises raw `TypeError` at `766-773`. |
| Nested collections deterministic | **Pass for valid records** | `json.dumps(sort_keys=True)` recursively sorts mapping keys; lists preserve order. |
| Schema name/version mandatory | **Pass** | Required set at `research_record.py:332-365`; checks at `483-490`. |
| Unsupported schema versions fail closed | **Pass** | `research_record.py:483-490`; tests at `tests/test_xauusd_p_strategy.py:1334-1343`. |
| Parsing malformed/inconsistent records | **Partial/fail** | JSON decode and schema checks work; `NaN`, side/time inconsistency, and unverified claims are accepted. |
| Round-trip claim | **Limited as documented** | `parse_research_record()` returns a mapping, not a `StrategyDecision`; documented at `docs/strategy/research_record_contract.md:298-300`. |
| Numeric meaning preserved | **Partial** | No numeric normalization; `100` and `100.0` produce different canonical JSON; non-finite values are accepted. |

### B.1 Cases where records could serialize differently or be accepted inconsistently

1. **`NaN` in optional/context numeric fields** remains accepted by `validate`; JSON parsing accepts it; canonicalization rejects later.
2. **Int versus float** produces different JSON bytes for the same numeric value.
3. **Extra fields** are accepted by validation; unsupported values fail only at JSON encoding.
4. **Candidate side/time** can be changed independently of the plan and top-level decision timestamp without rejection.
5. **`calendar_verified`, `news.safe`, `risk.sizing_status`, and `breakeven_costs_verified`** can be changed without rejection for the approved identity.

---

## 5. Audit C — Completeness and Research Safety

### C.1 Fields retained

| Required content | Present? | Evidence |
|---|---|---|
| Decision status and rejection reasons | **Yes** | `research_record.py:311-315`; rejections from `_reject()` at `xauusd_p.py:1525-1540`; test at `1437-1453`. |
| Strategy/variant identity | **Yes structurally** | `research_record.py:286-296`; provenance limitation in M1. |
| Decision timestamp and dataset-time semantics | **Yes, with documented gap** | `research_record.py:298-301`; `decision_time_original` defaults to ISO; raw source timestamp is not available. |
| Symbol and timeframe | **Yes** | `research_record.py:302-310`; timeframe is the hardcoded M15 decision timeframe. |
| Score/confidence/confluence | **Yes** | `research_record.py:75-90`; validator at `553-586`; tests at `1252-1255`. |
| Session/filter mode | **Yes, but not truth-validated** | `research_record.py:93-102`, `588-604`. |
| News/unaware/point | **Yes, partial safety** | `research_record.py:104-113`, `606-621`; research variant constrained, approved claims not. |
| Entry/stop/TP1/TP2/partials/BE | **Yes** | `research_record.py:115-149`; tests assert exact plan values at `1257-1272`. |
| Risk/sizing/limitations | **Yes structurally; partial safety** | `research_record.py:210-243`, `730-743`; sizing status unconstrained, plan blocked status enforced. |
| Research-only status | **Yes** | `research_record.py:290, 511-521`. |

### C.2 Candidate and `TradeSignal` authority

**Pass.** `serialize_strategy_decision()` requires `isinstance(decision, StrategyDecision)` and raises `TypeError` for candidate/`TradeSignal` inputs (`research_record.py:253-258`). The test at `tests/test_xauusd_p_strategy.py:1414-1421` verifies both cases. The record also carries `authoritative_source = "StrategyDecision"`.

### C.3 False claim assessment

| Claim | Can a record falsely assert it? | Evidence |
|---|---|---|
| Verified absolute UTC | **No, validator forces unverified** | `research_record.py:519-523`. |
| Execution readiness | **No, top-level and candidate forced false** | `research_record.py:513-514`, `671-674`; tests. |
| Research-only status | **No, forced true** | `research_record.py:511-512`. |
| Verified session calendar | **Yes** | `research_record.py:589-604` accepts approved `calendar_verified=true`. |
| News safety/availability | **Yes for approved identity** | `research_record.py:606-621` only constrains research-isolated identity. |
| Monetary sizing | **Plan blocked; risk status can still say otherwise** | Plan status enforced at `648-655`; `risk.sizing_status` only nonempty at `733-737`. |
| Cost verification | **Yes** | `plan.breakeven_costs_verified` only checked as boolean at `646-647`. |

The record therefore retains the required data but does not reliably encode the absence of unverified claims.

---

## 6. Audit D — Tests, Regression Boundaries, and Commands

### 6.1 Required commands and actual results

Run from `C:\xampp\htdocs\Trantor`:

```text
python -m pytest tests/test_xauusd_p_strategy.py -q
77 passed in 3.35s
```

```text
python -m pytest -q
426 passed in 15.42s
```

```text
python -m compileall -q src tests
successful; no output
```

```text
git diff --check
warning: in the working copy of 'README.md', LF will be replaced by CRLF the next time Git touches it
```

No whitespace errors were reported. The LF/CRLF warning is pre-existing for `README.md`. The repository baseline has project files untracked, so `git diff --check` does not inspect the untracked source and documentation files; the audit inspected those files directly.

### 6.2 Read-only audit probes

The probes ran only in process memory; no runtime code, tests, datasets, or configuration were modified.

```text
NaN in plan.breakeven:
  validate_research_record -> ACCEPTED
  canonical_json -> ValueError: Out of range float values are not JSON compliant: nan
  parse_research_record -> ACCEPTED with nan

NaN in serialized decision risk.risk_fraction:
  serialize_strategy_decision -> ACCEPTED
  canonical_json -> ValueError: Out of range float values are not JSON compliant: nan

session.calendar_verified = true -> ACCEPTED
risk.sizing_status = "sized" -> ACCEPTED
plan.breakeven_costs_verified = true -> ACCEPTED
approved news available/safe=true, unaware=false -> ACCEPTED

candidate side changed from buy to sell -> ACCEPTED
candidate decision_time changed independently -> ACCEPTED

variant.__dict__ identity replacement -> approved identity observed
object.__setattr__ identity replacement -> approved identity observed
```

### 6.3 Broker, execution, monetary, and data boundaries

The inspected implementation does not import or call broker/execution modules. `src/trantor/strategy/research_record.py` imports only standard-library serialization types, model enums, and strategy record types. `src/trantor/strategy/xauusd_p_research.py` contains no broker adapter, order method, spread, commission, financing, slippage, or account calculation.

No backtest, broker call, order action, monetary sizing, historical-data regeneration, acquisition-tool change, or account-setting change was performed by this audit. The tests are strategy/record tests only.

---

## 7. False-Positive or Overconfident Documentation Claims

1. **`docs/strategy/research_record_contract.md:62-70`** says strategy identity is “immutable at the instance level” and that assigning identity fields raises `AttributeError`. This is true for ordinary attribute assignment, but `variant.__dict__` and `object.__setattr__` bypass it, as demonstrated above. The statement should be limited to ordinary attribute assignment or removed.

2. **`docs/strategy/phase_e_readiness_review.md:322`** marks the canonical record as **READY** without recording the non-finite-number and unverified-claim findings. This should be **READY WITH FINDINGS** or **NOT READY** until H1 and H2 are resolved.

3. **`docs/strategy/phase_e_readiness_review.md:630`** repeats that strategy identity is immutable at the instance level without the object-model caveat.

4. **`docs/strategy/research_record_contract.md:279-300`** describes a canonical round-trip sequence. That sequence is accurate for valid finite records and for the parser’s documented mapping-only contract, but it does not disclose that `parse_research_record()` accepts `NaN` and inconsistent approved-identity claims.

5. **`docs/strategy/research_record_contract.md:252-255`** says canonical JSON uses no implementation-dependent defaults. This is true for serialization of a given finite record, but it omits the int/float representation difference and the raw error behavior for non-finite/unsupported values.

6. **`docs/strategy/phase_e_readiness_review.md:10`** states the record is “implemented and tested.” That is factually true, but the overall readiness implication is overconfident given the failed integrity probes.

---

## 8. Suitability as Future Research-Backtester Input

**Not yet suitable as the authoritative future research-backtester input.**

The record shape is a strong foundation and the main happy path is implemented, but a backtester must not consume this contract until at least:

1. all numeric fields reject non-finite values consistently in `serialize`, `validate`, `parse`, and `canonical_json`;
2. `parse_research_record()` rejects non-standard JSON constants and malformed/inconsistent records;
3. session/news/sizing/cost claim fields have explicit research-safe invariants or are sourced from a separately verified evidence object;
4. candidate side, stop/TP ordering, candidate/decision timestamps, and plan consistency are validated;
5. identity protection is described accurately and, if provenance is required, a producer-bound identity mechanism is designed rather than relying on a static name map.

The contract should remain usable for trusted, in-process strategy decisions after those fixes. It should not be treated as a tamper-proof external record format.

---

## 9. Explicit Remaining Blockers

- **Record integrity findings H1, H2, and M2–M3** must be resolved or explicitly accepted before this contract is used as a backtester source of truth.
- **Identity provenance** remains application-level only; no cryptographic or object-identity link exists.
- **Absolute UTC provenance** remains unverified. The record correctly labels dataset time but cannot resolve the underlying broker/MT5 epoch question.
- **Verified session/holiday calendar** remains unavailable.
- **Verified historical economic calendar** remains unavailable; approved records can currently claim news safety without an evidence field.
- **Spread/cost convention** remains unverified/blocked. `breakeven_costs_verified` is not constrained to false.
- **Monetary sizing and account-equity rules** remain blocked.
- **Broker/demo/live execution** remains blocked and out of scope.
- **No decision-object reconstruction** is provided by the parser; this is documented and acceptable for an archival mapping, but the future backtester must state whether it needs reconstruction.
- **Phase E backtester** is not implemented and remains unauthorized.
- **No backtest was run.**

---

## 10. Final Audit Position

The implementation is not a false claim: the serializer, schema, identity map, plan preservation, and most tests are present and working. The audit nevertheless returns **FAIL** for research-record integrity because the parser and validator accept records that the contract presents as canonical and safe, including non-finite numbers and unverified compliance/cost claims.

No Phase E implementation, backtest, broker action, monetary sizing, historical data change, acquisition-tool change, or account-setting change was performed. The only audit documentation artifact created is `docs/strategy/research_record_integrity_audit.md`; `compileall` may have refreshed bytecode caches, but no runtime source, test, dataset, manifest, acquisition tool, or broker setting was modified.

---

## 11. Post-Audit Remediation Status

This addendum records the corrective work performed after the audit. The original
**FAIL** verdict above applies to the audited pre-correction implementation.

| Finding | Status | Evidence |
|---|---|---|
| H1 non-finite numbers | **Resolved** | `_finite_float()` and `_normalize_numbers()` reject non-finite values; `json.loads(..., parse_constant=...)` rejects `NaN`/`Infinity`; serialization, validation, parsing, and canonicalization now fail closed with `ResearchRecordSchemaError`. |
| H2 unverified compliance/cost claims | **Resolved for schema 1.0** | Session verification, news availability/safety/point awards, risk-sizing status, cost adjustment, and breakeven cost verification are constrained; required limitation strings are enforced. News-aware approved records are rejected until an evidence-bearing schema exists. |
| M1 identity provenance | **Partially resolved / documented** | Structural identity checks remain; the contract now states that matching does not prove historical origin and that identity is not tamper-proof. |
| M2 identity object bypass | **Partially resolved / documented** | Ordinary assignment is blocked; direct `__dict__` and `object.__setattr__` bypasses remain Python object-model limitations, now disclosed. |
| M3 candidate/plan inconsistency | **Resolved** | Candidate/decision timestamp equality, entry-time ordering, confidence equality, risk-distance, side/stop/TP ordering, and breakeven relationship are validated. |
| L1 numeric representation | **Resolved** | Price/level/risk/ratio fields normalize to binary64 floats; integer-valued counts/score points remain integers; `100` and `100.0` canonicalize equivalently for normalized fields. |
| L2 unsupported fields/objects | **Resolved** | Unknown fields are rejected at top-level and nested boundaries; canonical JSON failures are translated to `ResearchRecordSchemaError`. |
| L3 duplicated identity maps | **Open (low)** | The hardcoded identity/filter-mode maps remain duplicated; this is a maintainability risk, not a current mismatch. |
| L4 test coverage | **Resolved** | Focused tests now cover finite values, parse/serialize failures, compliance claims, timestamp/direction/geometry consistency, partial fractions, breakeven, rejections, identity fields, unsupported fields/objects, and numeric canonicalization. |

Verification after correction:

```text
python -m pytest tests/test_xauusd_p_strategy.py -q
88 passed

python -m pytest -q
437 passed

python -m compileall -q src tests
successful

git diff --check
only the pre-existing README.md LF/CRLF warning
```
