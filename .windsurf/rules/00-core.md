# Risk Tech – Agent Readiness Rules

These rules apply whenever executing `/rt-agent-readiness`.

## Operating constraints

- Stay **local**: read files in the repo and run local CLI commands only.
- Prefer **deterministic** signals (file existence, config parsing, grep-able workflow text).
- If a signal requires repo-host settings (e.g., branch protection), mark it **Skipped** with a clear note.

## Output requirements

Always produce the following artifacts:

- `artifacts/rt-agent-readiness/<run-id>/outputs/readiness.json`
- `artifacts/rt-agent-readiness/<run-id>/outputs/report.md`
- `artifacts/rt-agent-readiness/<run-id>/outputs/report.html`

## How to reason about results

- A **criterion** is binary: Pass / Fail / Skipped.
- Pillar and level scores **exclude Skipped** criteria from denominators.
- Recommendations must be **actionable**: file paths, example snippets, and the fastest next steps.
