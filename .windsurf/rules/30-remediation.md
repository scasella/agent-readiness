# Remediation workflow safety rules

These rules apply when executing `/rt-agent-readiness-remediate`.

## Safe-by-default behavior

- Default mode is **plan-only**: generate remediation artifacts, make no repo changes.
- Apply mode (`--apply`) is **opt-in** and must only be run with explicit user approval.

## Allowed changes (apply mode)

Apply mode may only scaffold **missing** repo hygiene assets that are low risk and easy to review:
- Documentation: README.md, AGENTS.md, CONTRIBUTING.md, SECURITY.md
- GitHub metadata: .github/pull_request_template.md, .github/CODEOWNERS, .github/ISSUE_TEMPLATE/*
- Safe configuration scaffolds: .env.example, .pre-commit-config.yaml, .github/dependabot.yml, .gitleaks.toml
- Optional dev environment scaffold: .devcontainer/devcontainer.json

## Prohibited changes

- Do not modify application/source code.
- Do not overwrite existing files.
- Do not claim CI, tests, or releases ran unless you actually ran them.
- Do not add network-dependent tooling unless the repo already uses it.

## Traceability

- Always write `remediation_plan.md` and `remediation_plan.json` to the same run folder as the assessment.
- If applying changes, list exactly which files were created and which were skipped.
