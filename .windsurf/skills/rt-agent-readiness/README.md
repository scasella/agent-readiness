# /rt-agent-readiness pack

Drop this `.windsurf/` directory into a repository, then run:

- `/rt-agent-readiness`

## Optional configuration

Create `.rt-agent-readiness.json` at the repo root to override defaults.

Example:

```json
{
  "org_name": "Risk Tech",
  "repo_kind": "service",
  "app_roots": ["apps/api", "apps/web"],
  "exclude_globs": ["**/generated/**", "**/vendor/**"]
}
```

## Outputs

Each run writes to:

- `artifacts/rt-agent-readiness/<run-id>/outputs/`

Open `report.html` in your browser for the most readable view.
