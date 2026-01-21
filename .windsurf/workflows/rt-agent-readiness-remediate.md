# /rt-agent-readiness-remediate

Safely generate (and optionally apply) a remediation plan to improve **Risk Tech Agent Readiness**.

This workflow is **safe-by-default**:
- It always generates a fresh assessment run.
- It generates a remediation plan (**no repo changes**).
- Applying changes is **opt-in** and limited to scaffolding high-leverage repo hygiene assets
  (docs/templates/config) that are low-risk and easy to review.

## What this produces

A run folder under:

- `artifacts/rt-agent-readiness/<run-id>/`

Containing (in addition to the normal assessment artifacts):

- `outputs/remediation_plan.md` – human-friendly remediation plan
- `outputs/remediation_plan.json` – machine-readable remediation plan (files to create, notes)

If you choose to apply changes, the workflow will create a small set of files in the repo
(e.g., `AGENTS.md`, `.github/…`, `SECURITY.md`) and then you can open a PR.

## Execution steps

### 1) Generate a fresh assessment

Run:

```bash
python .windsurf/scripts/rt_agent_readiness.py --repo-root . --out artifacts/rt-agent-readiness
```

Copy the printed run directory path and call it `$RUN`.

### 2) Generate remediation plan (no repo changes)

Run:

```bash
python .windsurf/scripts/rt_agent_readiness_remediate.py --repo-root . --run-dir "$RUN"
```

This writes:
- `$RUN/outputs/remediation_plan.md`
- `$RUN/outputs/remediation_plan.json`

### 3) Preview and decide

In-chat, summarize:
- The **next maturity level** the repo is trying to reach
- The **top 5** recommended remediations (highest leverage)
- Which remediations are **auto-scaffoldable** vs **manual**

Then ask the user if they want to apply the safe scaffolding changes.

### 4) Optional: apply safe scaffolding changes

Only do this if the user explicitly approves.

Run:

```bash
python .windsurf/scripts/rt_agent_readiness_remediate.py --repo-root . --run-dir "$RUN" --apply
```

Notes:
- This mode only **creates missing files**; it does not overwrite existing files.
- It does not run network calls.
- It does not change application code.

### 5) Re-assess to confirm improvement

Run again:

```bash
python .windsurf/scripts/rt_agent_readiness.py --repo-root . --out artifacts/rt-agent-readiness
```

Compare the new report to the previous run.

### 6) Create a PR (manual)

Review the generated files, then:

```bash
git status
# review

git add .
git commit -m "Scaffold agent readiness hygiene"
```

Open a PR as normal.

## Safety constraints

- Do **not** apply changes without explicit approval.
- Do **not** claim tests or CI ran unless you ran them.
- If the repo has strict contribution processes, follow them.
