#!/usr/bin/env python3
"""Risk Tech – Agent Readiness remediation scaffolder.

This script reads the latest `readiness.json` produced by `rt_agent_readiness.py` and
produces a remediation plan. Optionally, it can scaffold a small set of **missing**
repo hygiene assets (docs/templates/config) to help the repository progress to the
next maturity level.

Design principles
- Safe-by-default: the default is plan-only (no repo changes)
- Apply mode is opt-in via `--apply`
- Apply mode only creates missing files (never overwrites)
- Offline: no network access required by this script
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------
# Helpers
# ----------------------------


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_read_text(path: Path, max_bytes: int = 400_000) -> str:
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(_safe_read_text(path))


def _write_text_if_missing(path: Path, content: str) -> Tuple[str, str]:
    """Return (status, note)."""
    if path.exists():
        return ("skipped_exists", f"Skipped (already exists): {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return ("created", f"Created: {path}")


def _render_template(template_path: Path, vars: Dict[str, str]) -> str:
    txt = _safe_read_text(template_path)
    for k, v in vars.items():
        txt = txt.replace("{{" + k + "}}", v)
    return txt


# ----------------------------
# Domain: commands + repo map
# ----------------------------


def _list_top_level_dirs(repo_root: Path) -> List[str]:
    out: List[str] = []
    for p in sorted(repo_root.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith("."):
            continue
        if name in {"node_modules", "dist", "build", "target", "venv", ".venv", "__pycache__"}:
            continue
        out.append(name + "/")
    return out[:20]


def _detect_standard_commands(repo_root: Path, apps: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Return a dict with keys: build, lint, typecheck, format, test, run.

    This is best-effort and intentionally conservative. If we cannot infer a
    reliable command, we provide a placeholder and mark it as TODO.
    """

    commands: Dict[str, List[str]] = {k: [] for k in ["build", "lint", "typecheck", "format", "test", "run"]}

    for app in apps:
        rel = app.get("path") or "."
        kind = app.get("kind") or "unknown"
        app_root = (repo_root / rel).resolve() if rel != "." else repo_root

        prefix = "" if rel == "." else f"(from {rel}) "

        if kind == "node":
            pkg = app_root / "package.json"
            scripts: Dict[str, Any] = {}
            if pkg.exists():
                try:
                    scripts = (_load_json(pkg).get("scripts") or {})
                except Exception:
                    scripts = {}

            def _script_or_fallback(name: str, fallback: str) -> str:
                if isinstance(scripts, dict) and name in scripts:
                    return f"{prefix}npm run {name}"
                return f"{prefix}{fallback}"

            commands["build"].append(_script_or_fallback("build", "# TODO: add a build script (e.g., npm run build)"))
            commands["lint"].append(_script_or_fallback("lint", "# TODO: add a lint script (e.g., npm run lint)"))
            commands["typecheck"].append(_script_or_fallback("typecheck", "# TODO: add a typecheck script (e.g., npm run typecheck)"))
            commands["format"].append(_script_or_fallback("format", "# TODO: add a format script (e.g., npm run format)"))
            commands["test"].append(_script_or_fallback("test", "npm test"))
            commands["run"].append(_script_or_fallback("start", "# TODO: document how to run the app"))

        elif kind == "python":
            # Prefer pyproject.toml presence
            req = app_root / "requirements.txt"
            pyproject = app_root / "pyproject.toml"

            if pyproject.exists():
                commands["build"].append(f"{prefix}# TODO: if packaging is required, document build command")
            else:
                commands["build"].append(f"{prefix}# TODO: document build/packaging (if applicable)")

            commands["lint"].append(f"{prefix}# TODO: run lint (e.g., ruff check .)")
            commands["typecheck"].append(f"{prefix}# TODO: run type checks (e.g., mypy .)")
            commands["format"].append(f"{prefix}# TODO: run formatter (e.g., ruff format . or black .)")

            if (app_root / "tests").exists() or any(app_root.rglob("test_*.py")):
                commands["test"].append(f"{prefix}python -m pytest")
            else:
                commands["test"].append(f"{prefix}# TODO: add and run tests (e.g., python -m pytest)")

            commands["run"].append(f"{prefix}# TODO: document how to run the app / service")

        elif kind == "go":
            commands["build"].append(f"{prefix}go build ./...")
            commands["lint"].append(f"{prefix}# TODO: run golangci-lint (if configured)")
            commands["typecheck"].append(f"{prefix}go test ./...  # includes compilation")
            commands["format"].append(f"{prefix}gofmt -w .")
            commands["test"].append(f"{prefix}go test ./...")
            commands["run"].append(f"{prefix}# TODO: document how to run the binary/service")

        elif kind == "rust":
            commands["build"].append(f"{prefix}cargo build")
            commands["lint"].append(f"{prefix}cargo clippy")
            commands["typecheck"].append(f"{prefix}cargo check")
            commands["format"].append(f"{prefix}cargo fmt")
            commands["test"].append(f"{prefix}cargo test")
            commands["run"].append(f"{prefix}cargo run")

        elif kind == "java":
            commands["build"].append(f"{prefix}# TODO: document build (e.g., ./gradlew build or mvn package)")
            commands["lint"].append(f"{prefix}# TODO: document lint/static analysis (if applicable)")
            commands["typecheck"].append(f"{prefix}# TODO: document compilation / static checks")
            commands["format"].append(f"{prefix}# TODO: document formatting")
            commands["test"].append(f"{prefix}# TODO: document tests (e.g., ./gradlew test or mvn test)")
            commands["run"].append(f"{prefix}# TODO: document how to run")

        else:
            # Unknown: keep placeholders
            commands["build"].append(f"{prefix}# TODO: document build")
            commands["lint"].append(f"{prefix}# TODO: document lint")
            commands["typecheck"].append(f"{prefix}# TODO: document typecheck")
            commands["format"].append(f"{prefix}# TODO: document format")
            commands["test"].append(f"{prefix}# TODO: document tests")
            commands["run"].append(f"{prefix}# TODO: document how to run")

    # De-dupe and keep stable order
    for k in list(commands.keys()):
        seen: List[str] = []
        for c in commands[k]:
            if c not in seen:
                seen.append(c)
        commands[k] = seen

    return commands


def _format_commands_block(cmds: Dict[str, List[str]]) -> str:
    lines: List[str] = []
    for k in ["build", "lint", "typecheck", "format", "test", "run"]:
        pretty = k.capitalize()
        lines.append(f"- **{pretty}:**")
        for c in cmds.get(k) or []:
            lines.append(f"  - `{c}`")
        if not (cmds.get(k) or []):
            lines.append("  - `# TODO: add command`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# ----------------------------
# Remediation plan model
# ----------------------------


@dataclass
class FileOp:
    path: str
    action: str  # create_if_missing
    template: str
    reason: str
    status: str = "planned"  # planned|created|skipped_exists
    note: str = ""


@dataclass
class RemediationItem:
    criterion_id: str
    title: str
    auto_scaffold: bool
    description: str
    file_ops: List[FileOp]
    manual_steps: List[str]


# ----------------------------
# Fix mapping
# ----------------------------


def _default_owner_from_cfg(repo_root: Path) -> str:
    cfg = repo_root / ".rt-agent-readiness.json"
    if cfg.exists():
        try:
            j = _load_json(cfg)
            owner = j.get("default_codeowner") or j.get("default_codeowners")
            if isinstance(owner, str) and owner.strip():
                return owner.strip().lstrip("@")
        except Exception:
            pass
    return "risk-tech-owners"  # placeholder


def build_file_ops_for_criterion(repo_root: Path, readiness: Dict[str, Any], criterion_id: str) -> Tuple[bool, List[FileOp], List[str], str]:
    """Return (auto_scaffold, file_ops, manual_steps, description)."""

    meta = readiness.get("meta") or {}
    repo_name = str(meta.get("repo_name") or repo_root.name)
    repo_desc = str(meta.get("description") or "")
    apps = meta.get("discovered_apps") or []

    vars_common = {
        "REPO_NAME": repo_name,
        "REPO_DESCRIPTION": repo_desc if repo_desc else "(TODO: add a short description of the repository’s purpose.)",
        "DEFAULT_OWNER": _default_owner_from_cfg(repo_root),
        "QUICKSTART_BLOCK": "```bash\n# TODO: document install + run\n```",
        "REPO_MAP_BLOCK": "\n".join([f"- `{d}` – (TODO: describe)" for d in _list_top_level_dirs(repo_root)]) or "- (TODO: add repo map)",
        "CI_BLOCK": "- CI should run lint/typecheck/tests on every PR.\n- Document how to reproduce CI checks locally.",
        "SETUP_BLOCK": "```bash\n# TODO: document local setup\n```",
        "COMMANDS_BLOCK": _format_commands_block(_detect_standard_commands(repo_root, apps)),
        "LICENSE_BLOCK": "(TODO: add license information or link to LICENSE file.)",
    }

    template_root = Path(__file__).resolve().parent.parent / "templates"

    def op(path: str, template: str, reason: str) -> FileOp:
        return FileOp(path=path, action="create_if_missing", template=template, reason=reason)

    manual: List[str] = []
    ops: List[FileOp] = []

    if criterion_id == "agents_md":
        ops.append(op("AGENTS.md", "AGENTS.md.template", "Provide agent-facing development instructions."))
        return True, ops, manual, "Add AGENTS.md so agents (and new engineers) have a single source of truth for commands, loops, and expectations."

    if criterion_id == "contributing":
        ops.append(op("CONTRIBUTING.md", "CONTRIBUTING.md.template", "Standardize contribution and PR expectations."))
        return True, ops, manual, "Add CONTRIBUTING.md to reduce review churn and make change flow explicit."

    if criterion_id == "pr_template":
        ops.append(op(".github/pull_request_template.md", "pull_request_template.md.template", "Make PR context and verification evidence consistent."))
        return True, ops, manual, "Add a PR template to consistently capture risk and verification evidence."

    if criterion_id == "issue_templates":
        ops.append(op(".github/ISSUE_TEMPLATE/bug_report.md", "ISSUE_TEMPLATE/bug_report.md.template", "Ensure bugs are reported with reproducible steps."))
        ops.append(op(".github/ISSUE_TEMPLATE/feature_request.md", "ISSUE_TEMPLATE/feature_request.md.template", "Ensure features are proposed with acceptance criteria."))
        ops.append(op(".github/ISSUE_TEMPLATE/incident_followup.md", "ISSUE_TEMPLATE/incident_followup.md.template", "Track incident follow-ups with action items."))
        ops.append(op(".github/ISSUE_TEMPLATE/config.yml", "ISSUE_TEMPLATE/config.yml.template", "Route security issues away from public trackers."))
        return True, ops, manual, "Add issue templates to improve issue quality and make work easier to pick up (for humans and agents)."

    if criterion_id == "codeowners":
        ops.append(op(".github/CODEOWNERS", "CODEOWNERS.template", "Define ownership to route reviews and approvals."))
        manual.append("Replace placeholder owners in .github/CODEOWNERS with real GitHub users/teams.")
        return True, ops, manual, "Add CODEOWNERS to make ownership explicit and enforceable."

    if criterion_id == "security_policy":
        ops.append(op("SECURITY.md", "SECURITY.md.template", "Provide a security reporting channel and policy."))
        manual.append("Update contact channels in SECURITY.md to match your organization.")
        return True, ops, manual, "Add SECURITY.md to standardize vulnerability reporting and reduce risk."

    if criterion_id == "env_template":
        ops.append(op(".env.example", "env.example.template", "Document required environment variables without secrets."))
        manual.append("Add required env vars with safe defaults in .env.example (do not include secrets).")
        return True, ops, manual, "Add an environment template so agents do not guess runtime configuration."

    if criterion_id == "devcontainer":
        ops.append(op(".devcontainer/devcontainer.json", "devcontainer.json.template", "Provide a reproducible dev environment."))
        manual.append("Customize devcontainer.json with language runtimes and tools required by your repo.")
        return True, ops, manual, "Add a devcontainer scaffold to reduce " + '"works on my machine"' + " issues."

    if criterion_id == "gitignore":
        ops.append(op(".gitignore", "gitignore.template", "Prevent committing secrets and build artifacts."))
        manual.append("Review .gitignore and tune for repo-specific tooling.")
        return True, ops, manual, "Add/update .gitignore to reduce accidental commits of secrets and noisy artifacts."

    if criterion_id == "readme":
        ops.append(op("README.md", "README.md.template", "Provide a canonical entry point for humans and agents."))
        manual.append("Update README.md with real setup/run commands and a short repo overview.")
        return True, ops, manual, "Add a README as a canonical entry point (purpose, quickstart, and links)."

    if criterion_id in ("pre_commit_hooks", "large_file_detection"):
        # A single pre-commit config can satisfy both criteria (depending on evaluator rules).
        ops.append(op(".pre-commit-config.yaml", "pre-commit-config.yaml.template", "Add local automation and large-file detection."))
        manual.append("If your environment is locked down, mirror pre-commit hook repos internally.")
        return True, ops, manual, "Add pre-commit hooks to prevent avoidable CI churn and accidental large file commits."

    if criterion_id == "dependabot":
        # Generated, not templated.
        ops.append(op(".github/dependabot.yml", "__generated_dependabot__", "Automate dependency update PRs."))
        return True, ops, manual, "Enable automated dependency update PRs to keep dependencies current with less manual effort."

    if criterion_id == "secret_scanning_tooling":
        ops.append(op(".gitleaks.toml", "gitleaks.toml.template", "Provide a baseline secret scanning configuration."))
        manual.append("Wire secret scanning into CI (or an internal pipeline) so it runs on PRs.")
        return True, ops, manual, "Add a secret scanning baseline and ensure it runs on PRs."

    # Default: manual
    manual.append("See the assessment report for recommended remediation steps.")
    return False, ops, manual, "This criterion is not auto-scaffoldable and likely requires repo-specific engineering work."


def _generate_dependabot_yaml(apps: List[Dict[str, Any]]) -> str:
    """Generate a conservative Dependabot config.

    Uses one weekly schedule per ecosystem root.
    """

    updates: List[Dict[str, str]] = []

    def add(ecosystem: str, directory: str) -> None:
        updates.append(
            {
                "package-ecosystem": ecosystem,
                "directory": directory,
                "schedule": "weekly",
            }
        )

    # Always check github-actions
    add("github-actions", "/")

    seen: set = set()
    for a in apps:
        rel = a.get("path") or "."
        dir_path = "/" if rel == "." else f"/{rel.strip('./')}"
        kind = a.get("kind") or "unknown"

        if kind == "node" and ("npm", dir_path) not in seen:
            add("npm", dir_path)
            seen.add(("npm", dir_path))
        if kind == "python" and ("pip", dir_path) not in seen:
            add("pip", dir_path)
            seen.add(("pip", dir_path))
        if kind == "go" and ("gomod", dir_path) not in seen:
            add("gomod", dir_path)
            seen.add(("gomod", dir_path))
        if kind == "rust" and ("cargo", dir_path) not in seen:
            add("cargo", dir_path)
            seen.add(("cargo", dir_path))

    # Render YAML manually (stdlib only)
    lines: List[str] = []
    lines.append("version: 2")
    lines.append("updates:")
    for u in updates:
        lines.append("  - package-ecosystem: \"%s\"" % u["package-ecosystem"])
        lines.append("    directory: \"%s\"" % u["directory"])
        lines.append("    schedule:")
        lines.append("      interval: \"%s\"" % u["schedule"])
    return "\n".join(lines) + "\n"


# ----------------------------
# Main
# ----------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", help="Path to repository root (default: .)")
    ap.add_argument("--run-dir", required=True, help="Run directory produced by rt_agent_readiness.py")
    ap.add_argument("--apply", action="store_true", help="Create missing files (never overwrites)")
    ap.add_argument("--max-items", type=int, default=10, help="Max remediation items to include")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    run_dir = Path(args.run_dir).resolve()

    readiness_path = run_dir / "outputs" / "readiness.json"
    if not readiness_path.exists():
        print(f"[rt-agent-readiness][FAIL] Missing readiness.json at: {readiness_path}")
        return 2

    readiness = _load_json(readiness_path)

    meta = readiness.get("meta") or {}
    repo_name = str(meta.get("repo_name") or repo_root.name)
    apps = meta.get("discovered_apps") or []

    scores = (readiness.get("scores") or {})
    overall = scores.get("overall") or {}
    level_achieved = int(scores.get("level_achieved") or 1)

    # Determine blocking + target levels
    blocking_level = level_achieved if level_achieved < 5 else 5
    target_level = min(blocking_level + 1, 5)

    # Use action_items if present; otherwise fall back to failing criteria at blocking level
    action_items = readiness.get("action_items") or []

    # Expand action_items to include more than 3 if needed
    # If action_items is empty, derive from criteria list.
    if not action_items:
        criteria = readiness.get("criteria") or []
        fails = [c for c in criteria if c.get("status") == "fail" and int(c.get("level") or 0) == blocking_level]
        # Sort by weight desc
        fails.sort(key=lambda c: (-int(c.get("weight") or 0), str(c.get("pillar") or ""), str(c.get("id") or "")))
        action_items = [
            {
                "criterion_id": c.get("id"),
                "title": c.get("title"),
                "pillar": c.get("pillar"),
                "why": c.get("why"),
                "remediation": c.get("remediation"),
            }
            for c in fails[: max(args.max_items, 3)]
        ]

    # Build remediation items
    items: List[RemediationItem] = []
    file_ops: List[FileOp] = []

    for ai in action_items[: args.max_items]:
        cid = str(ai.get("criterion_id") or "")
        if not cid:
            continue
        title = str(ai.get("title") or cid)

        auto, ops, manual, desc = build_file_ops_for_criterion(repo_root, readiness, cid)
        items.append(RemediationItem(criterion_id=cid, title=title, auto_scaffold=auto, description=desc, file_ops=ops, manual_steps=manual))
        for o in ops:
            file_ops.append(o)

    # Prepare outputs
    out_md = run_dir / "outputs" / "remediation_plan.md"
    out_json = run_dir / "outputs" / "remediation_plan.json"

    template_root = Path(__file__).resolve().parent.parent / "templates"

    # Apply (optional)
    if args.apply:
        for op in file_ops:
            dest = repo_root / op.path
            if op.template == "__generated_dependabot__":
                content = _generate_dependabot_yaml(apps)
                status, note = _write_text_if_missing(dest, content)
                op.status = status
                op.note = note
                continue

            tpl_path = template_root / op.template
            if not tpl_path.exists():
                op.status = "skipped_missing_template"
                op.note = f"Template not found: {op.template}"
                continue

            # Vars for placeholders
            vars_common = {
                "REPO_NAME": repo_name,
                "REPO_DESCRIPTION": str(meta.get("description") or "(TODO: add a short repository description.)"),
                "DEFAULT_OWNER": _default_owner_from_cfg(repo_root),
                "QUICKSTART_BLOCK": "```bash\n# TODO: document install + run\n```",
                "REPO_MAP_BLOCK": "\n".join([f"- `{d}` – (TODO: describe)" for d in _list_top_level_dirs(repo_root)]) or "- (TODO: add repo map)",
                "CI_BLOCK": "- CI should run lint/typecheck/tests on every PR.\n- Document how to reproduce CI checks locally.",
                "SETUP_BLOCK": "```bash\n# TODO: document local setup\n```",
                "COMMANDS_BLOCK": _format_commands_block(_detect_standard_commands(repo_root, apps)),
                "LICENSE_BLOCK": "(TODO: add license information or link to LICENSE file.)",
            }

            content = _render_template(tpl_path, vars_common)
            status, note = _write_text_if_missing(dest, content)
            op.status = status
            op.note = note

    # Render plan markdown
    lines: List[str] = []
    lines.append(f"# Risk Tech – Agent Readiness remediation plan")
    lines.append("")
    lines.append(f"**Repository:** `{repo_name}`")
    lines.append(f"**Generated:** {_utc_now_iso()}")
    lines.append(f"**Assessment run:** `{run_dir.name}`")
    lines.append("")
    lines.append("## Current state")
    lines.append("")
    lines.append(f"- **Level achieved:** {level_achieved} / 5")
    if overall:
        lines.append(f"- **Overall pass rate:** {overall.get('percent')}% ({overall.get('passed')}/{overall.get('total')})")
    lines.append(f"- **Blocking level:** L{blocking_level} (must reach ≥80% to unlock L{target_level})")
    lines.append("")

    lines.append("## Recommended remediations")
    lines.append("")
    for it in items:
        badge = "AUTO" if it.auto_scaffold and it.file_ops else "MANUAL"
        lines.append(f"### {it.criterion_id} — {it.title} ({badge})")
        lines.append("")
        lines.append(it.description)
        lines.append("")
        if it.file_ops:
            lines.append("**Suggested file operations:**")
            for op in it.file_ops:
                status = op.status
                if not args.apply:
                    status = "planned"
                lines.append(f"- `{op.path}` — {op.action} ({status})")
                if op.reason:
                    lines.append(f"  - Reason: {op.reason}")
                if args.apply and op.note:
                    lines.append(f"  - Result: {op.note}")
            lines.append("")
        if it.manual_steps:
            lines.append("**Manual follow-ups:**")
            for ms in it.manual_steps:
                lines.append(f"- {ms}")
            lines.append("")

    if not items:
        lines.append("No remediation items were generated. This usually means the report had no action_items and no failing criteria at the blocking level.")
        lines.append("")

    if not args.apply:
        lines.append("## Apply mode")
        lines.append("")
        lines.append("To scaffold the safe, missing files listed above (without overwriting anything), re-run:")
        lines.append("")
        lines.append("```bash")
        lines.append(f"python .windsurf/scripts/rt_agent_readiness_remediate.py --repo-root . --run-dir \"{run_dir}\" --apply")
        lines.append("```")
        lines.append("")
        lines.append("Then review changes with `git status` and open a PR.")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Render plan json
    plan = {
        "framework": {
            "name": "Risk Tech – Agent Readiness remediation",
            "version": "1.1.0",
            "mode": "apply" if args.apply else "plan",
        },
        "generated_at": _utc_now_iso(),
        "repo": {
            "name": repo_name,
            "path": str(repo_root),
        },
        "assessment_run": {
            "run_dir": str(run_dir),
            "run_id": str(run_dir.name),
        },
        "summary": {
            "level_achieved": level_achieved,
            "overall": overall,
            "blocking_level": blocking_level,
            "target_level": target_level,
        },
        "items": [
            {
                "criterion_id": i.criterion_id,
                "title": i.title,
                "auto_scaffold": i.auto_scaffold,
                "description": i.description,
                "file_ops": [
                    {
                        "path": fo.path,
                        "action": fo.action,
                        "template": fo.template,
                        "reason": fo.reason,
                        "status": fo.status if args.apply else "planned",
                        "note": fo.note if args.apply else "",
                    }
                    for fo in i.file_ops
                ],
                "manual_steps": i.manual_steps,
            }
            for i in items
        ],
    }

    out_json.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    print(f"[rt-agent-readiness] Remediation plan written:")
    print(f"  - {out_md}")
    print(f"  - {out_json}")
    if args.apply:
        print("[rt-agent-readiness] Apply mode complete. Review changes and open a PR.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
