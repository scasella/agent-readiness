# /rt-agent-readiness

Generate a Risk Tech–branded Agent Readiness assessment for the **current repository** using only local signals.

## What this produces

A run folder under:

- `artifacts/rt-agent-readiness/<run-id>/`

Containing:

- `outputs/readiness.json` – machine-readable scorecard (criteria, pillars, levels, action items)
- `outputs/report.md` – human-friendly Markdown report
- `outputs/report.html` – visually polished HTML report (single file, no external assets)

## Inputs (optional)

If present at repo root, this file can tune evaluation behavior:

- `.rt-agent-readiness.json`

Supported keys (all optional):

- `app_roots`: explicit list of application roots (monorepo override)
- `exclude_globs`: additional path globs to ignore during scanning
- `repo_kind`: `"service" | "library" | "cli" | "monorepo"`
- `org_name`: string used for branding in the report

## Execution steps

1) **Create a run directory + evaluate**
- Run:
  - `python .windsurf/scripts/rt_agent_readiness.py --repo-root . --out artifacts/rt-agent-readiness`

2) **Validate outputs**
- Run:
  - `python .windsurf/scripts/validate_outputs.py --run-dir <the run dir printed by the evaluator>`

3) **Summarize results in-chat**
- Report:
  - Level achieved (1–5)
  - Overall pass rate
  - Top 3 strengths (pillars)
  - Top 3 opportunities (criteria)
  - Next 3 action items to reach the next level

4) **Point to artifacts**
- Provide paths to:
  - `report.html`
  - `report.md`
  - `readiness.json`

## Notes

- Some governance signals require repo-host settings. If local evaluation can’t verify them, they will be marked **Skipped**.
- This pack is assessment-only by default. It writes only under `artifacts/rt-agent-readiness/`.
