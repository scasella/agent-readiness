# Skill: Agent Readiness remediation

This skill supports `/rt-agent-readiness-remediate`.

## Goal

Turn an Agent Readiness assessment into a concrete, reviewable improvement plan.

## Operating constraints

- Default to **plan-only** mode
- Only scaffold missing repo hygiene assets when `--apply` is explicitly approved
- Never overwrite existing files
- Do not change application code

## Outputs

Write to the same assessment run folder:

- `outputs/remediation_plan.md`
- `outputs/remediation_plan.json`

## What counts as success

- The plan clearly identifies the **blocking level** and the fastest path to unlock the next
- The plan separates **auto-scaffoldable** vs **manual** work
- In apply mode, every created file is listed explicitly (and nothing is overwritten)
