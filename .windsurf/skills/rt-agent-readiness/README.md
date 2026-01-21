# Risk Tech – Agent Readiness pack

Drop this `.windsurf/` directory into a repository, then run:

- `/rt-agent-readiness` (assessment)

If you want an improvement plan (and optional safe scaffolding), run:

- `/rt-agent-readiness-remediate`

## Optional configuration

Create `.rt-agent-readiness.json` at the repo root to override defaults.

Example:

```json
{
  "org_name": "Risk Tech",
  "repo_kind": "service",
  "app_roots": ["apps/api", "apps/web"],
  "exclude_globs": ["**/generated/**", "**/vendor/**"],
  "app_discovery_max_depth": 4
}
```

## Outputs

Each assessment run writes to:

- `artifacts/rt-agent-readiness/<run-id>/outputs/`

Open `report.html` in your browser for the most readable view.

Remediation planning writes additional artifacts:

- `outputs/remediation_plan.md`
- `outputs/remediation_plan.json`
