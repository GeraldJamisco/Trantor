# Trantor Architecture Package

- **Status:** Owner accepted the design. WP1 profile/protocol/runtime safety, WP2 canonical research-record integration, and WP3 backtester hardening are implemented and verified. The authoritative pre-run checkpoint passed, including mandatory decision-record/linkage validation and a deterministic small-window replay. The full historical experiment has not been run and awaits owner authorization. Paper/demo/live execution remains disabled and not implemented.
- **Date:** 2026-11-02
- **Authoritative instrument:** `XAUUSD-P` on the Fortune Prime Limited MT5 environment.
- **Current strategy:** `XAUUSDPMultiTimeframeStrategy` and `XAUUSDPMTFResearchVariant`.
- **Current research-record schema:** `trantor.research_decision` version `1.0`.
- **Current Phase E status:** Deterministic gross price/R backtester implemented; full historical experiment and holdout not run.

This directory defines the complete target system, current-state gaps, component
contracts, state models, safety boundaries, implementation sequence, traceability,
and the final design review. It does not implement runtime components and does
not run a backtest.

## Document map

| Document | Purpose |
|---|---|
| `system_architecture.md` | System boundaries, component map, repository layout, data flows, modes, dependencies |
| `component_contracts.md` | Purpose, inputs, outputs, dependencies, state, failures, safety, tests, completion criteria for every component |
| `state_interfaces_persistence.md` | Interface contracts, state models, trade lifecycle, persistence, identifiers, recovery |
| `risk_safety_configuration.md` | Risk hierarchy, execution barriers, configuration specification, secrets, mode separation |
| `implementation_roadmap.md` | Dependency-ordered work packages, completion criteria, external-fact gates |
| `requirements_traceability.md` | Approved requirements mapped to components, work packages and tests |
| `known_limitations_register.md` | Unresolved facts, blockers, owners, and impact by stage |
| `design_acceptance_report.md` | Direct answers to the final acceptance questions and design recommendation |

## Source-of-truth documents

The design does not duplicate or replace the approved strategy rules:

- Strategy specification: `docs/strategy/xauusd_p_strategy_spec_v1.md`
- Phase C decisions: `docs/strategy/phase_c_decision_report.md`
- Phase D implementation audit: `docs/strategy/phase_d_implementation_audit.md`
- Phase E plan: `docs/strategy/phase_e_backtest_plan.md`
- Phase E readiness: `docs/strategy/phase_e_readiness_review.md`
- Research-record contract: `docs/strategy/research_record_contract.md`
- Backtester implementation: `docs/strategy/phase_e_backtest_implementation.md`
- Timestamp verification: `docs/strategy/task_1_targeted_verification.md`
- Spread verification: `docs/strategy/task_2_spread_verification.md`
- D1/D2 reconciliation and prerequisite verification: `docs/strategy/phase_e_prerequisite_verification.md`

Where these documents and the current repository differ, the conflict is
recorded in `known_limitations_register.md`; this package does not silently
choose a new rule.

## Baseline evidence

Read-only baseline at design time:

```text
python -m pytest -q
450 passed

python -m compileall -q src tests
successful

git diff --check
only the pre-existing README.md LF/CRLF warning
```

No historical backtest was run. No broker order, account setting, dataset,
manifest, strategy threshold, or execution path was changed while producing this
package.

## Review gate

Implementation may begin only after the owner accepts this design package and
explicitly resolves the design decisions marked as blocking in
`known_limitations_register.md`. The first implementation work package is
repository/interface correction, not the live trading loop.
