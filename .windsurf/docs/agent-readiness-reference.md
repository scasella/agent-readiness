# Agent Readiness reference (internal)

This pack implements a practical agent readiness maturity model that:

- Scores repositories across **8 technical pillars**
- Stages progress across **5 maturity levels**
- Uses **binary criteria** with **Skipped** for inapplicable or non-local signals
- Produces both human-friendly and machine-readable artifacts

## Eight pillars

1. Style & Validation
2. Build System
3. Testing
4. Documentation
5. Dev Environment
6. Code Quality
7. Observability
8. Security & Governance

## Five maturity levels

- L1: Functional
- L2: Documented
- L3: Standardized
- L4: Optimized
- L5: Autonomous

## What is “Skipped”?

Some governance signals (e.g., protected branch settings) typically live in repo-host settings.
When running locally in an IDE, those cannot be verified reliably. The evaluator marks these as **Skipped**
and excludes them from score denominators.

If your organization can supply that metadata through an approved channel, the evaluator can be extended later
to incorporate it.

## Progression targets

The report includes:

- `level_achieved` – the current unlocked maturity level
- `blocking_level` – the level that must be improved to unlock the next
- `next_level_target` – the level that becomes unlockable once the blocking level reaches ≥80%

Action items are chosen from the **blocking level** criteria to maximize the speed at which the next
level becomes unlockable.

## Remediation workflow

`/rt-agent-readiness-remediate` generates a remediation plan and can optionally scaffold a small set of
missing repo hygiene assets (docs/templates/config) without overwriting existing files.
