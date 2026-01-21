---
id: rt-agent-readiness
version: 1.0.0
title: Risk Tech – Agent Readiness Assessment
---

# Risk Tech – Agent Readiness Assessment (Skill)

This skill supports the `/rt-agent-readiness` workflow by providing:

- A **maturity model** (5 levels)
- A **technical pillar model** (8 pillars)
- A **deterministic evaluation** implementation (Python, standard library only)
- A **professional report format** (HTML + Markdown + JSON)

## Why this exists (and why it’s more than a prompt)

A shared prompt library can describe *what* to look for, but it cannot reliably:

- Discover monorepo applications and score criteria `n/d`
- Parse repo configuration files deterministically
- Produce consistent, repeatable scores over time
- Generate an auditable, machine-readable scorecard for dashboards and tracking
- Enforce output structure and quality gates

This pack behaves more like a **program**:
- The workflow is the “command”
- Criteria are the “spec”
- The evaluator is the “engine”
- The report is the “artifact”

## The model: 8 pillars

Agent readiness is evaluated across **eight pillars**. Each pillar represents an agent failure mode that can be prevented by making the environment more explicit, reproducible, and self-validating.

1) **Style & Validation**
   - Linters, formatters, type checkers, and local validation hooks.
   - Goal: catch issues in seconds, not minutes later in CI.

2) **Build System**
   - Deterministic build commands, pinned dependencies, CI setup, and release automation.
   - Goal: agents can build and package changes without guessing.

3) **Testing**
   - Unit/integration tests, coverage, and test ergonomics.
   - Goal: tight feedback loops so agents can iterate safely.

4) **Documentation**
   - README, development instructions, and discoverable operational knowledge.
   - Goal: reduce tribal knowledge and “ask a human” bottlenecks.

5) **Dev Environment**
   - Reproducible environments (e.g., devcontainers), env templates, local service bootstrapping.
   - Goal: prevent “works on my machine” and enable reliable local verification.

6) **Code Quality**
   - Modularization, complexity controls, dead/duplicate code detection, tech debt tracking.
   - Goal: keep the codebase navigable for both humans and agents.

7) **Observability**
   - Structured logs, metrics, traces, runbooks, alerting signals.
   - Goal: make runtime behavior explainable and debuggable.

8) **Security & Governance**
   - Ownership, automated scanning, dependency updates, safe secret handling patterns.
   - Goal: enable safe acceleration (fast + controlled).

## The model: 5 maturity levels

Readiness is staged across five levels. Each level reflects what agents can accomplish safely.

- **Level 1 – Functional**
  - Code builds/runs; baseline tools exist.
- **Level 2 – Documented**
  - Setup, workflows, and expectations are written down; basic automation exists.
- **Level 3 – Standardized**
  - Processes are defined and enforced via automation. This is the practical “production-grade” target.
- **Level 4 – Optimized**
  - Fast feedback and continuous measurement; systems are tuned for productivity.
- **Level 5 – Autonomous**
  - Self-improving systems and orchestrated maintenance (rare; typically incremental).

## Scoring rules

- Criteria are **binary**: Pass / Fail / Skipped.
- Many criteria are evaluated per application in a monorepo and reported as `n/d`.
- Pillar and level pass rates **exclude Skipped** criteria from denominators.
- “Level achieved” reflects **gated progression**:
  - Passing a level’s prerequisites unlocks the next level (this mirrors how readiness gates are typically applied in practice).

## Extending the pack

- Add or adjust criteria in:
  - `.windsurf/scripts/rt_agent_readiness.py` (see `CRITERIA` list)
- Keep changes deterministic when possible.
- Prefer new criteria that:
  - Agents can remediate with a PR
  - Have clear, repo-local evidence


## Optional remediation workflow

This pack includes an optional workflow:

- `/rt-agent-readiness-remediate`

It is safe-by-default:
- It generates a remediation plan (no changes)
- Applying changes requires an explicit `--apply` flag
- The apply mode only scaffolds *missing* repo hygiene assets (docs/templates/config)
  and does not overwrite existing files

The goal is to remove common friction for agents (unclear commands, missing templates, missing ownership)
without risking unintended changes to product code.
