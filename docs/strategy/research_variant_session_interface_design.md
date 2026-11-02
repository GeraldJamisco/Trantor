# Research Variant Session Interface Design

- **Date:** 2026-11-02
- **Repository:** `C:\xampp\htdocs\Trantor`
- **Instrument:** `XAUUSD-P` only
- **Status:** Research-only interface implemented and tested. No backtester, dataset, broker, monetary-sizing, or execution changes.
- **Approved strategy:** remains unchanged and retains its mandatory session filter.

---

## 1. Executive Summary

The approved strategy still enforces the session filter in `XAUUSDPMultiTimeframeStrategy.evaluate`; `research_mode=True` does not disable the IANA overlap. There is no public session-disable switch on the approved class.

The smallest safe interface has now been implemented:

1. the existing session decision was extracted into a protected method on the approved strategy;
2. the approved method and all default behavior are unchanged;
3. a separate, explicitly named `XAUUSDPMTFResearchVariant` overrides only the session decision hook and forces news-unaware behavior;
4. `SessionEvaluation.filter_mode` and `StrategyDecision.variant_id` provide structured distinction.

The approved class remains subject to `SESSION_OUTSIDE` and `SESSION_UNVERIFIED`; the research variant is a separate class with a stable ID/version and research-only labels.

This implementation does not authorize the Phase E backtester or any execution behavior.

---

## 2. Current Implementation and Enforcement Path

### 2.1 Session calculation

`_session_overlap` is a module-level function at `src/trantor/strategy/xauusd_p.py:1161–1177`.

It:

- treats `decision_time` and `entry_time` as timezone-aware timestamps;
- converts the relevant local dates to London/New York `ZoneInfo` windows;
- returns `(decision_in_overlap, entry_in_overlap)`.

The configured windows are in `XAUUSDPMTFParameters` (`london_start/end`, `new_york_start/end`). No broker holiday calendar is present.

### 2.2 Enforcement in `evaluate`

`XAUUSDPMultiTimeframeStrategy.evaluate` currently:

- accepts `research_mode: bool = True` and `session_calendar_verified: bool = False` at `src/trantor/strategy/xauusd_p.py:1538–1550`;
- calls the protected session hook at `1949`;
- builds the approved `SessionEvaluation` in `_session_evaluation` at `2148–2176`;
- rejects outside-overlap decisions with `SESSION_OUTSIDE` at `1965`;
- rejects unverified-calendar production decisions with `SESSION_UNVERIFIED` at `1980`.

The important behavior is:

```text
research_mode=True, session_calendar_verified=False:
    still rejects outside IANA overlap
    allows inside overlap but makes no verified-session claim

research_mode=False, session_calendar_verified=False:
    rejects outside overlap and unverified calendar
```

`session_calendar_verified` is a caller-supplied flag, not proof of a verified broker session calendar.

### 2.3 News filtering

`_news_evaluation` at `1180–1242` already supports a research news-unaware path:

- unavailable/stale/incomplete calendar data yields no point;
- with `research_mode=True` and `news_unaware=True`, no news point and no safety pause are claimed;
- production fails closed.

The existing news flag does not disable session filtering.

### 2.4 Decision record

`SessionFilterMode` and `SessionEvaluation` are at `src/trantor/strategy/xauusd_p.py:441–456`:

```text
in_overlap
calendar_verified
research_allowed
reason
filter_mode (approved or research_isolated)
```

`StrategyDecision` contains the session evaluation and `variant_id` at `600–616`. `XAUUSDPSignalCandidate` contains the resulting research plan at `555–596`.

---

## 3. Implemented Smallest Safe Interface

### 3.1 Approved strategy default

The approved class retains no `disable_session=True` or equivalent public boolean. It keeps its existing constructor/evaluate signature behavior and mandatory session checks.

The session decision is now extracted into the protected method:

```text
_evaluate_session(decision_time, entry_time, ...)
    -> SessionEvaluation
```

The default implementation reproduces the prior `_session_overlap` + `SessionEvaluation` logic, and the outer `SESSION_OUTSIDE` and `SESSION_UNVERIFIED` checks remain in `evaluate`.

### 3.2 Separate research variant

Implemented in a separate module:

```text
src/trantor/strategy/xauusd_p_research.py
XAUUSDPMTFResearchVariant
name = "xauusd_p_multi_timeframe_research_filter_isolated"
version = "1.0.0"
variant_id = "xauusd_p_filter_isolated_v1"
```

It overrides only the session hook and returns:

```text
in_overlap = True
calendar_verified = False
research_allowed = True
filter_mode = RESEARCH_ISOLATED
reason = "Research variant: session filter isolated; not verified session compliance."
```

It forces the news-unaware research path (`news_context=None`, `research_mode=True`, `news_unaware=True`). A supplied news context is ignored and cannot earn the news point. A caller requesting `session_calendar_verified=True` receives a `ValueError`; the variant cannot claim verified session compliance.

The variant must be imported and instantiated explicitly. It is not selected by a string, environment variable, or global switch.

### 3.3 Session-policy record

Implemented in `src/trantor/strategy/xauusd_p.py`:

```text
SessionFilterMode.APPROVED
SessionFilterMode.RESEARCH_ISOLATED
```

The approved strategy records `APPROVED`; the research variant records `RESEARCH_ISOLATED`. `StrategyDecision.variant_id` additionally records `xauusd_p_approved_v1` or `xauusd_p_filter_isolated_v1`.

The Phase E record envelope must additionally include strategy class/name/version, news mode, dataset-time label, and no-complete-strategy/no-session-compliance flags.

### 3.4 Why this is safer than a switch

- The approved class has no public disable argument.
- The research variant is a distinct type with a distinct name and record label.
- A caller cannot accidentally turn off session filtering by changing a boolean on the approved strategy.
- The base tests can continue to assert mandatory rejection.
- The variant behavior is visible in the decision session record and report.

---

## 4. Approved vs Research Variant Behavior

| Behavior | Approved strategy | Research variant |
|---|---|---|
| Session filter | Mandatory IANA overlap | Isolated/disabled |
| Session compliance claim | None unless separately verified | Never claimed |
| News filter | Existing production/research behavior | Explicitly news-unaware; no news point |
| News compliance claim | Not available | Never claimed |
| Other strategy rules | Unchanged | Unchanged |
| Decision status | Can reject `SESSION_OUTSIDE` / `SESSION_UNVERIFIED` | Does not reject for session isolation |
| Session record | `APPROVED` | `RESEARCH_ISOLATED` |
| Report label | Normal research label | `RESEARCH-ONLY FILTER VARIANT - NOT COMPLETE APPROVED STRATEGY` |
| Execution readiness | False | False |

The research variant is not an implementation of the complete approved strategy. It is a labelled research experiment with one or more mandatory filters isolated.

---

## 5. Timestamp and Calendar Limitations

- The stored timestamps are `dataset_time`; absolute UTC provenance is not verified.
- IANA session windows are not proof of broker session compliance.
- No verified XAUUSD-P holiday/session calendar exists.
- No verified historical economic calendar exists.
- Disabling session/news filters removes the immediate dependency on those calendars for eligibility, but does not verify timestamps, daily boundaries, split boundaries, bar completion, or data provenance.
- Even in the research variant, `decision_time` remains the dataset-time coordinate used for:
  - completed-bar filtering;
  - 00:00 research-day accounting;
  - chronological split assignment;
  - cross-timeframe alignment.
- News-unaware mode must never award the news-confluence point.

No absolute UTC or session/news compliance claim may be attached to the research variant.

---

## 6. Required Tests

### 6.1 Approved strategy retains mandatory filtering

- Base strategy outside IANA overlap returns `SESSION_OUTSIDE` even with `research_mode=True`, `session_calendar_verified=False`, and `news_unaware=True`.
- Base strategy inside overlap but with `research_mode=False` and `session_calendar_verified=False` returns `SESSION_UNVERIFIED`.
- Base strategy inside overlap with the research flags produces only research-allowed decisions and still records `APPROVED` filter mode.
- Existing session tests remain in `tests/test_xauusd_p_strategy.py` and continue to pass.

### 6.2 Research variant is explicitly isolated

- Research variant at an outside-overlap timestamp produces no `SESSION_OUTSIDE` rejection.
- Its `SessionEvaluation.filter_mode` is `RESEARCH_ISOLATED`.
- Its decision record/report contains the distinct variant ID and label.
- Its news evaluation has no point and no verified-news claim even if a news context is supplied.
- It remains `execution_ready=False` and has no monetary sizing.

### 6.3 Separation and safety

- The approved class rejects an attempt to construct it with a nonexistent session-disable argument.
- No base-class code path selects the research variant implicitly.
- Research variant cannot be selected by environment variable or untyped string.
- Reports label the variant and do not claim complete-strategy or session compliance.
- No execution/broker imports or order methods appear in either class.
- Decision records contain enough information to reconstruct which session policy was applied.

### 6.4 Regression and integration

- Existing full strategy suite passes.
- Prefix/full replay equality still holds.
- No news point is awarded in the variant.
- Cross-timeframe, split and daily-time behavior remains unchanged by the session hook.
- A future harness records a deterministic variant/session mode.

---

## 7. Files Changed by Implementation

Implemented:

- `src/trantor/strategy/xauusd_p.py`
  - added `SessionFilterMode`;
  - added `SessionEvaluation.filter_mode`;
  - added `StrategyDecision.variant_id`;
  - extracted `_session_evaluation` while preserving approved behavior.
- `src/trantor/strategy/xauusd_p_research.py`
  - added the explicit research variant, stable identity and isolated session/news behavior.
- `src/trantor/strategy/research_record.py`
  - added the canonical, versioned research-decision record serializer and validator.
- `tests/test_xauusd_p_strategy.py`
  - added approved-vs-variant session, metadata, news, identity-integrity and record-serialization tests.
- `docs/strategy/research_variant_session_interface_design.md`
  - updated implementation and test record.
- `docs/strategy/research_record_contract.md`
  - records the canonical schema/version and backtester boundary.
- `docs/strategy/phase_e_readiness_review.md`
  - updated to record the implemented interface and current Phase E authorization status.

No broker, execution, safety, acquisition, dataset, dependency, strategy-parameter, or Phase E plan file was modified.

---

## 8. Resolved Decisions and Remaining Work

Implemented decisions:

1. Research variant lives in the separate `xauusd_p_research.py` module.
2. `SessionEvaluation.filter_mode` records the session policy; `StrategyDecision.variant_id` records the variant.
3. A supplied news context is ignored; the variant always uses news-unaware behavior and never awards the point.
4. `session_calendar_verified=True` is rejected by the variant; the variant cannot claim verified session compliance.
5. Stable names are fixed:
   - variant name: `xauusd_p_multi_timeframe_research_filter_isolated`
   - version: `1.0.0`
   - variant ID: `xauusd_p_filter_isolated_v1`
6. The research variant remains a labelled variant, not the complete approved strategy.

Remaining work is outside this implementation:

- Phase E harness/report integration;
- final session/news report labels and acceptance gates;
- timestamp provenance, spread, calendar and monetary blockers documented in the readiness review.

The canonical research-decision record is documented separately in
`docs/strategy/research_record_contract.md` and implemented in
`src/trantor/strategy/research_record.py`. It preserves variant identity,
session filter mode, strategy name/version, decision timing semantics, plan and
limitations. Serialization tests are included in the strategy test suite.

### Recommendation

The research-only interface is implemented and tested. The approved strategy retains its mandatory session filter; only the separately named research variant isolates it.

This implementation does not authorize the Phase E backtester or any execution behavior. Owner review is required before backtester implementation.

---

## 9. Files Changed

```text
src/trantor/strategy/xauusd_p.py
src/trantor/strategy/xauusd_p_research.py
src/trantor/strategy/research_record.py
tests/test_xauusd_p_strategy.py
docs/strategy/research_variant_session_interface_design.md
docs/strategy/research_record_contract.md
docs/strategy/phase_e_readiness_review.md
```

No datasets, broker settings, safety controls, acquisition tools, dependencies, or execution paths were modified.

---

## 10. Verification and Safety Confirmation

Actual test results:

```text
$env:PYTHONPATH='src'; python -m pytest tests/test_xauusd_p_strategy.py -q
77 passed
```

```text
$env:PYTHONPATH='src'; python -m pytest -q
426 passed
```

Safety:

- No backtest was implemented or run.
- No orders were submitted, modified, cancelled, or simulated through a broker.
- No monetary sizing was calculated.
- No datasets or manifests were modified.
- No broker or account settings were changed.
- `git diff --check` was run; only the pre-existing `README.md` LF/CRLF warning is present.
- Repository status contains the pre-existing `README.md` modification and untracked project directories.

**Stop here. Phase E backtester implementation and execution remain unauthorized.**
