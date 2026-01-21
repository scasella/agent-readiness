\
#!/usr/bin/env python3
"""
Risk Tech – Agent Readiness evaluator

- Deterministic, repo-local evaluation where possible
- Produces:
  - readiness.json (structured)
  - report.md (human-readable)
  - report.html (single-file, styled)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import fnmatch
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore


# ----------------------------
# Model definitions
# ----------------------------

PILLARS: List[Dict[str, str]] = [
    {
        "id": "style_validation",
        "name": "Style & Validation",
        "why": "Fast, local feedback (lint/format/typecheck) prevents agents from iterating blindly on avoidable errors.",
        "what_it_catches": "Syntax/style drift, type errors, low-signal CI failures.",
    },
    {
        "id": "build_system",
        "name": "Build System",
        "why": "Deterministic build and release paths let agents verify changes end-to-end without tribal knowledge.",
        "what_it_catches": "Unclear build commands, unpinned deps, missing CI/release automation.",
    },
    {
        "id": "testing",
        "name": "Testing",
        "why": "Tests are the safety net that lets agents move fast without breaking behavior.",
        "what_it_catches": "Lack of unit/integration tests, brittle or slow test loops.",
    },
    {
        "id": "documentation",
        "name": "Documentation",
        "why": "Written instructions replace oral tradition. Agents need explicit setup/run/deploy/debug guidance.",
        "what_it_catches": "Missing setup steps, undocumented env vars, unclear operational procedures.",
    },
    {
        "id": "dev_environment",
        "name": "Dev Environment",
        "why": "Reproducible environments eliminate 'works on my machine' and make agent verification reliable.",
        "what_it_catches": "Inconsistent local setup, missing env templates, undocumented local services.",
    },
    {
        "id": "code_quality",
        "name": "Code Quality",
        "why": "Agents scale better in modular, low-complexity codebases with explicit architectural boundaries.",
        "what_it_catches": "High complexity, dead/duplicate code, weak module boundaries, unmanaged tech debt.",
    },
    {
        "id": "observability",
        "name": "Observability",
        "why": "Logs/metrics/traces turn failures into explanations. Agents need runtime visibility to debug effectively.",
        "what_it_catches": "Opaque runtime errors, lack of runbooks, missing telemetry and alert signals.",
    },
    {
        "id": "security_governance",
        "name": "Security & Governance",
        "why": "Acceleration without guardrails increases risk. Ownership and automated scanning keep velocity safe.",
        "what_it_catches": "Missing ownership, weak review boundaries, absent scanning and dependency hygiene.",
    },
]

LEVELS: List[Dict[str, str]] = [
    {
        "level": 1,
        "name": "Functional",
        "description": "Baseline tooling exists; code can run/build/test with manual effort.",
        "agent_capability": "Small, supervised changes; limited self-validation.",
    },
    {
        "level": 2,
        "name": "Documented",
        "description": "Setup and workflows are written down; basic automation exists.",
        "agent_capability": "Reliable onboarding; agents can follow documented commands.",
    },
    {
        "level": 3,
        "name": "Standardized",
        "description": "Processes are defined and enforced through automation (practical production target).",
        "agent_capability": "Routine maintenance (bug fixes, tests, docs, dependency upgrades) with tight feedback loops.",
    },
    {
        "level": 4,
        "name": "Optimized",
        "description": "Fast feedback and continuous measurement; systems tuned for productivity.",
        "agent_capability": "Larger refactors and feature work with strong verification and faster iteration.",
    },
    {
        "level": 5,
        "name": "Autonomous",
        "description": "Self-improving systems and orchestrated maintenance; minimal human intervention.",
        "agent_capability": "Proactive improvement and parallelized work decomposition (rare; incremental).",
    },
]


# ----------------------------
# Utilities
# ----------------------------

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    ".tox",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "vendor",
    ".idea",
    ".vscode",
}

DEFAULT_EXCLUDE_GLOBS = [
    "**/.git/**",
    "**/node_modules/**",
    "**/dist/**",
    "**/build/**",
    "**/target/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.tox/**",
    "**/__pycache__/**",
]


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _short_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:10]


def _safe_read_text(path: Path, max_bytes: int = 200_000) -> str:
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


def _run_git(repo_root: Path, args: List[str]) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:
        return 1, str(e)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_safe_read_text(path))
    except Exception:
        return None


def _load_toml(path: Path) -> Optional[Dict[str, Any]]:
    if tomllib is None:
        return None
    try:
        return tomllib.loads(_safe_read_text(path))
    except Exception:
        return None


def _glob(repo_root: Path, pattern: str) -> List[Path]:
    # Use Path.glob with recursive patterns.
    try:
        return list(repo_root.glob(pattern))
    except Exception:
        return []


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _matches_any(path_str: str, globs: List[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path_str, g):
            return True
    return False


# ----------------------------
# Inventory (repo + apps)
# ----------------------------

@dataclass
class App:
    path: str  # relative
    kind: str  # "node" | "python" | "go" | "rust" | "java" | "dotnet" | "unknown"
    name: str
    description: str


@dataclass
class RepoMeta:
    repo_root: str
    repo_name: str
    description: str
    commit_sha: str
    default_branch: str
    detected_languages: List[str]
    discovered_apps: List[App]
    run_id: str
    generated_at: str
    org_name: str


def load_optional_config(repo_root: Path) -> Dict[str, Any]:
    cfg_path = repo_root / ".rt-agent-readiness.json"
    if not cfg_path.exists():
        return {}
    cfg = _load_json(cfg_path)
    return cfg or {}


def detect_repo_name(repo_root: Path) -> str:
    # Prefer remote origin repo name, fallback to folder name.
    rc, out = _run_git(repo_root, ["remote", "get-url", "origin"])
    if rc == 0 and out:
        # Handles git@github.com:org/repo.git and https://github.com/org/repo.git
        m = re.search(r"[:/](?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", out.strip())
        if m:
            return m.group("repo")
    return repo_root.name


def detect_repo_description(repo_root: Path) -> str:
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = repo_root / name
        if p.exists():
            text = _safe_read_text(p, max_bytes=50_000)
            # crude: first non-empty, non-header line
            for line in text.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    continue
                return s[:200]
    return ""


def discover_apps(repo_root: Path, cfg: Dict[str, Any]) -> List[App]:
    # Manual override
    if isinstance(cfg.get("app_roots"), list) and cfg["app_roots"]:
        apps: List[App] = []
        for rel in cfg["app_roots"]:
            app_root = (repo_root / rel).resolve()
            apps.append(describe_app(repo_root, app_root))
        return _dedupe_apps(apps)

    manifests = [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "*.csproj",
    ]

    max_depth = int(cfg.get("app_discovery_max_depth", 4))
    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
    for d in cfg.get("exclude_dirs", []) or []:
        exclude_dirs.add(str(d))

    apps: List[App] = []

    # Walk with depth control.
    for root, dirs, files in os.walk(repo_root):
        root_path = Path(root)
        rel_root = _rel(repo_root, root_path)
        depth = 0 if rel_root == "." else rel_root.count("/") + 1
        if depth > max_depth:
            dirs[:] = []
            continue

        # Prune dirs.
        pruned: List[str] = []
        for d in list(dirs):
            if d in exclude_dirs:
                pruned.append(d)
        for d in pruned:
            dirs.remove(d)

        # Skip excluded globs.
        if _matches_any(rel_root + "/", DEFAULT_EXCLUDE_GLOBS + (cfg.get("exclude_globs") or [])):
            dirs[:] = []
            continue

        for m in manifests:
            # simple match: exact or wildcard
            if "*" in m:
                for f in files:
                    if fnmatch.fnmatch(f, m):
                        apps.append(describe_app(repo_root, root_path))
                        break
            else:
                if m in files:
                    apps.append(describe_app(repo_root, root_path))
                    break

    # If no apps found, treat repo root as a single "app"
    if not apps:
        apps = [describe_app(repo_root, repo_root)]

    return _dedupe_apps(apps)


def _dedupe_apps(apps: List[App]) -> List[App]:
    # Deduplicate by path; keep the shallowest instance.
    seen: Dict[str, App] = {}
    for a in apps:
        if a.path not in seen:
            seen[a.path] = a
        else:
            # prefer shorter path description if conflict
            if len(a.path) < len(seen[a.path].path):
                seen[a.path] = a
    # stable ordering: by path length then lexicographic
    return sorted(seen.values(), key=lambda x: (x.path.count("/"), x.path))


def describe_app(repo_root: Path, app_root: Path) -> App:
    rel = _rel(repo_root, app_root)
    kind = "unknown"
    name = rel if rel != "." else repo_root.name
    desc = ""

    pkg = app_root / "package.json"
    pyproj = app_root / "pyproject.toml"
    req = app_root / "requirements.txt"
    gomod = app_root / "go.mod"
    cargo = app_root / "Cargo.toml"

    if pkg.exists():
        kind = "node"
        j = _load_json(pkg) or {}
        name = (j.get("name") or name)
        desc = (j.get("description") or "")
    elif pyproj.exists() or req.exists():
        kind = "python"
        if pyproj.exists():
            t = _load_toml(pyproj) or {}
            proj = (t.get("project") or {})
            name = (proj.get("name") or name)
            desc = (proj.get("description") or "")
    elif gomod.exists():
        kind = "go"
        text = _safe_read_text(gomod, max_bytes=50_000)
        m = re.search(r"^\s*module\s+(.+)\s*$", text, flags=re.MULTILINE)
        if m:
            name = m.group(1).strip()
    elif cargo.exists():
        kind = "rust"
        t = _load_toml(cargo) or {}
        pkg_tbl = (t.get("package") or {})
        name = (pkg_tbl.get("name") or name)
        desc = (pkg_tbl.get("description") or "")
    elif (app_root / "pom.xml").exists() or (app_root / "build.gradle").exists() or (app_root / "build.gradle.kts").exists():
        kind = "java"
    else:
        # Heuristic: infer by source file presence
        if any(p.suffix in {".py"} for p in app_root.glob("*.py")):
            kind = "python"
        elif any(p.suffix in {".go"} for p in app_root.glob("*.go")):
            kind = "go"
        elif any(p.suffix in {".rs"} for p in app_root.glob("*.rs")):
            kind = "rust"

    return App(path=rel, kind=kind, name=str(name), description=str(desc))


def detect_languages(apps: List[App]) -> List[str]:
    langs: List[str] = []
    for a in apps:
        if a.kind == "node":
            langs.append("JavaScript/TypeScript")
        elif a.kind == "python":
            langs.append("Python")
        elif a.kind == "go":
            langs.append("Go")
        elif a.kind == "rust":
            langs.append("Rust")
        elif a.kind == "java":
            langs.append("Java")
        elif a.kind == "dotnet":
            langs.append(".NET")
        else:
            pass
    # de-dupe stable
    out: List[str] = []
    for l in langs:
        if l not in out:
            out.append(l)
    return out


# ----------------------------
# Criteria engine
# ----------------------------

@dataclass
class EvalUnitResult:
    unit: str  # app path or "repo"
    status: str  # "pass"|"fail"|"skip"
    reason: str
    evidence: List[str]


@dataclass
class CriterionResult:
    id: str
    title: str
    pillar: str
    level: int
    scope: str  # "repo"|"app"
    weight: int
    numerator: int
    denominator: int
    status: str  # "pass"|"fail"|"skip"
    reason: str
    remediation: str
    why: str
    unit_results: List[EvalUnitResult]


def _make_unit(unit: str, status: str, reason: str, evidence: Optional[List[str]] = None) -> EvalUnitResult:
    return EvalUnitResult(unit=unit, status=status, reason=reason, evidence=evidence or [])


def _criterion_status_from_units(units: List[EvalUnitResult]) -> Tuple[int, int, str]:
    # Exclude skipped from denominator
    denom = sum(1 for u in units if u.status in ("pass", "fail"))
    num = sum(1 for u in units if u.status == "pass")
    if denom == 0:
        return 0, 0, "skip"
    if num == denom:
        return num, denom, "pass"
    return num, denom, "fail"


def _exists_any(root: Path, rel_paths: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for rp in rel_paths:
        p = root / rp
        if p.exists():
            hits.append(str(rp))
    return (len(hits) > 0), hits


def _glob_any(root: Path, patterns: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for pat in patterns:
        for p in root.glob(pat):
            if p.exists():
                hits.append(_rel(root, p))
    # de-dupe
    uniq: List[str] = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    return (len(uniq) > 0), uniq


def _text_any(root: Path, rel_files: List[str], needles: List[str]) -> Tuple[bool, List[str]]:
    found_in: List[str] = []
    for rf in rel_files:
        p = root / rf
        if not p.exists():
            continue
        txt = _safe_read_text(p, max_bytes=200_000).lower()
        for n in needles:
            if n.lower() in txt:
                found_in.append(rf)
                break
    uniq: List[str] = []
    for h in found_in:
        if h not in uniq:
            uniq.append(h)
    return (len(uniq) > 0), uniq


def _workflow_text_contains(repo_root: Path, needles: List[str]) -> Tuple[bool, List[str]]:
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.exists():
        return False, []
    hits: List[str] = []
    for wf in wf_dir.glob("*.y*ml"):
        txt = _safe_read_text(wf, max_bytes=400_000).lower()
        if all(n.lower() in txt for n in needles):
            hits.append(_rel(repo_root, wf))
    return (len(hits) > 0), hits


def _package_json_has_script(app_root: Path, script_name: str) -> bool:
    p = app_root / "package.json"
    if not p.exists():
        return False
    j = _load_json(p) or {}
    scripts = j.get("scripts") or {}
    return isinstance(scripts, dict) and script_name in scripts


def _pyproject_has_tool(app_root: Path, tool_key: str) -> bool:
    p = app_root / "pyproject.toml"
    if not p.exists() or tomllib is None:
        return False
    t = _load_toml(p) or {}
    tool = t.get("tool") or {}
    return isinstance(tool, dict) and tool_key in tool


def _tsconfig_strict(app_root: Path) -> bool:
    # Search typical tsconfig paths
    candidates = [
        app_root / "tsconfig.json",
        app_root / "tsconfig.base.json",
        app_root / "tsconfig.app.json",
    ]
    for c in candidates:
        if not c.exists():
            continue
        j = _load_json(c) or {}
        co = j.get("compilerOptions") or {}
        if isinstance(co, dict):
            if co.get("strict") is True:
                return True
            # strict family
            if co.get("noImplicitAny") is True and co.get("strictNullChecks") is True:
                return True
    return False


def _has_go_tests(app_root: Path) -> bool:
    # Quick scan: *_test.go under app root
    for p in app_root.rglob("*_test.go"):
        if DEFAULT_EXCLUDE_DIRS.intersection(p.parts):
            continue
        return True
    return False


def _has_py_tests(app_root: Path) -> bool:
    # tests/ dir or test_*.py
    if (app_root / "tests").exists():
        return True
    for p in app_root.rglob("test_*.py"):
        if DEFAULT_EXCLUDE_DIRS.intersection(p.parts):
            continue
        return True
    return False


def _has_node_tests(app_root: Path) -> bool:
    # test script in package.json OR common test dirs
    if _package_json_has_script(app_root, "test"):
        return True
    for d in ["test", "tests", "__tests__"]:
        if (app_root / d).exists():
            return True
    return False


def _has_integration_tests(app_root: Path) -> bool:
    for d in ["integration", "integration_tests", "e2e", "cypress", "playwright", "tests/integration", "tests/e2e"]:
        if (app_root / d).exists():
            return True
    # Playwright config
    if any((app_root / n).exists() for n in ["playwright.config.ts", "playwright.config.js"]):
        return True
    return False


def _has_devcontainer(repo_root: Path) -> bool:
    return (repo_root / ".devcontainer" / "devcontainer.json").exists()


def _has_env_template(repo_root: Path) -> bool:
    for name in [".env.example", ".env.template", ".env.sample", "env.example", "config/.env.example"]:
        if (repo_root / name).exists():
            return True
    return False


def _has_codeowners(repo_root: Path) -> bool:
    return (repo_root / "CODEOWNERS").exists() or (repo_root / ".github" / "CODEOWNERS").exists()


def _has_dependabot_or_renovate(repo_root: Path) -> bool:
    if (repo_root / ".github" / "dependabot.yml").exists():
        return True
    if (repo_root / "renovate.json").exists() or (repo_root / ".github" / "renovate.json").exists():
        return True
    return False


def _has_sast_config(repo_root: Path) -> bool:
    # Common: CodeQL workflow, semgrep config/workflow
    if (repo_root / ".github" / "workflows" / "codeql.yml").exists() or (repo_root / ".github" / "workflows" / "codeql.yaml").exists():
        return True
    if (repo_root / ".semgrep.yml").exists() or (repo_root / ".semgrep.yaml").exists():
        return True
    # semgrep in workflows
    ok, _ = _workflow_text_contains(repo_root, ["semgrep"])
    return ok


def _has_secret_scanning_tooling(repo_root: Path) -> bool:
    # Local detectable tools/config
    for name in [".gitleaks.toml", ".gitleaks.yml", ".gitleaks.yaml", "gitleaks.toml"]:
        if (repo_root / name).exists():
            return True
    ok, _ = _workflow_text_contains(repo_root, ["gitleaks"])
    return ok


def _has_logging_lib(app_root: Path) -> bool:
    # Heuristic by language
    if (app_root / "go.mod").exists():
        txt = _safe_read_text(app_root / "go.mod", max_bytes=200_000).lower()
        return any(k in txt for k in ["uber-go/zap", "sirupsen/logrus", "rs/zerolog", "go.uber.org/zap"])
    if (app_root / "pyproject.toml").exists():
        txt = _safe_read_text(app_root / "pyproject.toml", max_bytes=200_000).lower()
        return any(k in txt for k in ["structlog", "loguru"])
    if (app_root / "package.json").exists():
        txt = _safe_read_text(app_root / "package.json", max_bytes=200_000).lower()
        return any(k in txt for k in ["pino", "winston", "bunyan"])
    return False


def _has_metrics_lib(app_root: Path) -> bool:
    if (app_root / "go.mod").exists():
        txt = _safe_read_text(app_root / "go.mod", max_bytes=200_000).lower()
        return any(k in txt for k in ["prometheus", "opentelemetry", "datadog", "statsd"])
    if (app_root / "pyproject.toml").exists():
        txt = _safe_read_text(app_root / "pyproject.toml", max_bytes=200_000).lower()
        return any(k in txt for k in ["prometheus", "opentelemetry", "datadog", "statsd"])
    if (app_root / "package.json").exists():
        txt = _safe_read_text(app_root / "package.json", max_bytes=200_000).lower()
        return any(k in txt for k in ["prom-client", "opentelemetry", "datadog", "statsd"])
    return False


def _has_tracing_lib(app_root: Path) -> bool:
    if (app_root / "go.mod").exists():
        txt = _safe_read_text(app_root / "go.mod", max_bytes=200_000).lower()
        return "opentelemetry" in txt
    if (app_root / "pyproject.toml").exists():
        txt = _safe_read_text(app_root / "pyproject.toml", max_bytes=200_000).lower()
        return "opentelemetry" in txt
    if (app_root / "package.json").exists():
        txt = _safe_read_text(app_root / "package.json", max_bytes=200_000).lower()
        return "opentelemetry" in txt
    return False


def _has_error_tracking(app_root: Path) -> bool:
    # Sentry / Bugsnag / Rollbar etc
    candidates = ["sentry", "bugsnag", "rollbar", "honeybadger"]
    for file in ["package.json", "pyproject.toml", "requirements.txt", "go.mod"]:
        p = app_root / file
        if not p.exists():
            continue
        txt = _safe_read_text(p, max_bytes=200_000).lower()
        if any(c in txt for c in candidates):
            return True
    return False


def _has_runbooks(repo_root: Path) -> bool:
    for d in ["runbooks", "runbook", "ops/runbooks", "docs/runbooks", "playbooks", "docs/playbooks"]:
        if (repo_root / d).exists():
            return True
    # Link in docs
    ok, _ = _text_any(repo_root, ["README.md", "AGENTS.md", "docs/README.md"], ["runbook", "playbook"])
    return ok


def _has_ci(repo_root: Path) -> bool:
    if (repo_root / ".github" / "workflows").exists():
        return True
    if (repo_root / ".gitlab-ci.yml").exists():
        return True
    if (repo_root / "azure-pipelines.yml").exists():
        return True
    return False


def _has_release_automation(repo_root: Path) -> bool:
    # Common: goreleaser, semantic-release, changesets, release workflows
    if (repo_root / ".goreleaser.yml").exists() or (repo_root / ".goreleaser.yaml").exists():
        return True
    if (repo_root / ".changeset").exists() or (repo_root / ".changesets").exists():
        return True
    if (repo_root / "changesets").exists():
        return True
    if (repo_root / "release-please-config.json").exists():
        return True
    ok, _ = _workflow_text_contains(repo_root, ["release"])
    return ok


def _has_release_notes_automation(repo_root: Path) -> bool:
    # Presence of changelog tooling or workflow steps that generate notes
    if (repo_root / "CHANGELOG.md").exists():
        return True
    ok, _ = _workflow_text_contains(repo_root, ["changelog"])
    if ok:
        return True
    ok2, _ = _workflow_text_contains(repo_root, ["changeset"])
    return ok2


def _has_doc_gen_automation(repo_root: Path) -> bool:
    # Look for docs build/deploy workflows or generators
    ok, hits = _workflow_text_contains(repo_root, ["mkdocs"])  # common
    if ok:
        return True
    ok, hits = _workflow_text_contains(repo_root, ["sphinx"])
    if ok:
        return True
    ok, hits = _workflow_text_contains(repo_root, ["docusaurus"])
    if ok:
        return True
    ok, hits = _workflow_text_contains(repo_root, ["docs"])
    if ok and hits:
        return True
    return False


def _has_diagrams(repo_root: Path) -> bool:
    pats = ["**/*.mermaid", "**/*.mmd", "**/*.puml", "**/*.plantuml", "**/*.drawio", "**/architecture/**"]
    ok, hits = _glob_any(repo_root, pats)
    if ok and hits:
        return True
    # docs mention architecture
    ok2, _ = _text_any(repo_root, ["README.md", "docs/README.md", "AGENTS.md"], ["architecture", "system design", "diagram"])
    return ok2


def _has_precommit(repo_root: Path, app_root: Path) -> bool:
    # repo-level: .pre-commit-config.yaml OR husky/lefthook
    if (repo_root / ".pre-commit-config.yaml").exists():
        return True
    # Node: husky in package.json
    pkg = app_root / "package.json"
    if pkg.exists():
        j = _load_json(pkg) or {}
        if "husky" in j or (isinstance(j.get("devDependencies"), dict) and "husky" in j.get("devDependencies")):
            return True
        if (app_root / ".husky").exists():
            return True
    # lefthook
    if (repo_root / "lefthook.yml").exists() or (repo_root / "lefthook.yaml").exists():
        return True
    return False


def _has_linter(app_root: Path) -> bool:
    # Node/TS
    if (app_root / "package.json").exists():
        # eslint config
        if any((app_root / p).exists() for p in [".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs"]):
            return True
        if any((app_root / p).exists() for p in ["biome.json", ".biome.json"]):
            return True
    # Python
    if (app_root / "pyproject.toml").exists():
        if _pyproject_has_tool(app_root, "ruff") or _pyproject_has_tool(app_root, "flake8") or _pyproject_has_tool(app_root, "pylint"):
            return True
    if any((app_root / p).exists() for p in ["setup.cfg", "tox.ini", ".pylintrc"]):
        return True
    # Go
    if any((app_root / p).exists() for p in [".golangci.yml", ".golangci.yaml"]):
        return True
    ok, _ = _workflow_text_contains(app_root, ["golangci-lint"])
    if ok:
        return True
    # Rust
    if (app_root / "Cargo.toml").exists():
        # clippy in workflows
        ok, _ = _workflow_text_contains(app_root, ["clippy"])
        if ok:
            return True
    return False


def _has_formatter(app_root: Path) -> bool:
    # Node
    if (app_root / "package.json").exists():
        if any((app_root / p).exists() for p in [".prettierrc", ".prettierrc.json", ".prettierrc.yml", ".prettierrc.yaml", ".prettierrc.js", "prettier.config.js"]):
            return True
        if any((app_root / p).exists() for p in ["biome.json", ".biome.json"]):
            return True
    # Python
    if (app_root / "pyproject.toml").exists():
        if _pyproject_has_tool(app_root, "black") or _pyproject_has_tool(app_root, "ruff"):
            # ruff can format in newer versions; treat as formatter if configured
            return True
    # Go: gofmt exists by default if go.mod or go files
    if (app_root / "go.mod").exists():
        return True
    if any(p.suffix == ".go" for p in app_root.glob("*.go")):
        return True
    # Rust: rustfmt by default
    if (app_root / "Cargo.toml").exists():
        return True
    return False


def _has_typecheck(app_root: Path) -> bool:
    # TS
    if _tsconfig_strict(app_root):
        return True
    # Python mypy/pyright
    if (app_root / "pyproject.toml").exists():
        if _pyproject_has_tool(app_root, "mypy") or _pyproject_has_tool(app_root, "pyright"):
            return True
    if any((app_root / p).exists() for p in ["mypy.ini", "pyrightconfig.json"]):
        return True
    # Go and Rust compile-time
    if (app_root / "go.mod").exists() or (app_root / "Cargo.toml").exists():
        return True
    return False


def _has_strict_typing(app_root: Path) -> bool:
    # TS strict or Python mypy strict
    if _tsconfig_strict(app_root):
        return True
    if (app_root / "pyproject.toml").exists() and tomllib is not None:
        t = _load_toml(app_root / "pyproject.toml") or {}
        tool = t.get("tool") or {}
        mypy = tool.get("mypy") if isinstance(tool, dict) else None
        if isinstance(mypy, dict) and mypy.get("strict") is True:
            return True
    # Go/Rust are strict by default
    if (app_root / "go.mod").exists() or (app_root / "Cargo.toml").exists():
        return True
    return False


def _deps_pinned(repo_root: Path, app_root: Path) -> bool:
    # Repo-level check: any lockfiles in repo root or app root
    lockfiles = [
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "requirements.lock",
        "go.sum",
        "Cargo.lock",
        "Gemfile.lock",
    ]
    for lf in lockfiles:
        if (repo_root / lf).exists():
            return True
        if (app_root / lf).exists():
            return True
    return False


def _build_cmd_documented(repo_root: Path, app_root: Path) -> bool:
    # Heuristic: either known build script/target exists OR docs mention build command.
    # Node scripts
    if _package_json_has_script(app_root, "build") or _package_json_has_script(app_root, "compile"):
        return True
    # Makefile targets (very rough)
    if (repo_root / "Makefile").exists():
        mk = _safe_read_text(repo_root / "Makefile", max_bytes=200_000).lower()
        if re.search(r"^build\s*:", mk, flags=re.MULTILINE):
            return True
    # Docs mention
    ok, _ = _text_any(
        repo_root,
        ["README.md", "AGENTS.md"],
        ["npm run build", "pnpm build", "yarn build", "make build", "go build", "cargo build", "gradle build", "mvn package"],
    )
    return ok


def _single_command_setup(repo_root: Path, app_root: Path) -> bool:
    # Heuristic: docs have a single copy/paste line for bootstrap (make/dev scripts etc).
    needles = [
        "make setup",
        "make dev",
        "make bootstrap",
        "docker compose up",
        "docker-compose up",
        "pnpm install",
        "npm install",
        "uv sync",
        "poetry install",
        "pip install -r",
        "go mod download",
        "cargo build",
    ]
    ok, files = _text_any(repo_root, ["README.md", "AGENTS.md"], needles)
    return ok


def _gitignore_comprehensive(repo_root: Path) -> bool:
    p = repo_root / ".gitignore"
    if not p.exists():
        return False
    txt = _safe_read_text(p, max_bytes=200_000).lower()
    # Minimal set for most repos
    required_any = [
        "node_modules",
        ".env",
        ".ds_store",
        ".idea",
        ".vscode",
        "__pycache__",
        "dist",
        "build",
    ]
    hits = sum(1 for r in required_any if r in txt)
    return hits >= 3


def _documentation_freshness(repo_root: Path, days: int = 180) -> Tuple[bool, str]:
    # Use git log to check if README/AGENTS/CONTRIBUTING updated recently.
    files = ["README.md", "AGENTS.md", "CONTRIBUTING.md"]
    existing = [f for f in files if (repo_root / f).exists()]
    if not existing:
        return False, "No README/AGENTS/CONTRIBUTING files found to evaluate freshness."
    rc, out = _run_git(repo_root, ["log", "-1", "--format=%ct", "--", *existing])
    if rc != 0 or not out.strip().isdigit():
        return False, "Unable to evaluate freshness (git history unavailable)."
    ts = int(out.strip())
    age_days = int((_dt.datetime.utcnow().timestamp() - ts) / 86400)
    if age_days <= days:
        return True, f"Docs updated {age_days} days ago (≤ {days})."
    return False, f"Docs updated {age_days} days ago (> {days})."


def _has_pr_template(repo_root: Path) -> bool:
    candidates = [
        repo_root / ".github" / "pull_request_template.md",
        repo_root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        repo_root / "PULL_REQUEST_TEMPLATE.md",
    ]
    return any(p.exists() for p in candidates)


def _has_issue_templates(repo_root: Path) -> bool:
    return (repo_root / ".github" / "ISSUE_TEMPLATE").exists()


def _has_security_policy(repo_root: Path) -> bool:
    return (repo_root / "SECURITY.md").exists()


def _has_precommit_large_file_detection(repo_root: Path) -> bool:
    # Heuristic: pre-commit hook or git-lfs attributes
    if (repo_root / ".gitattributes").exists():
        txt = _safe_read_text(repo_root / ".gitattributes", max_bytes=50_000).lower()
        if "lfs" in txt:
            return True
    # pre-commit hook check-added-large-files
    p = repo_root / ".pre-commit-config.yaml"
    if p.exists():
        txt = _safe_read_text(p, max_bytes=200_000).lower()
        if "check-added-large-files" in txt:
            return True
    return False


def _has_complexity_tool(repo_root: Path) -> bool:
    # Rough: look for common tool names in workflows/config
    patterns = ["radon", "lizard", "gocyclo", "eslint.*complexity", "sonarqube"]
    # Search workflows
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if any(p in txt for p in patterns):
                return True
    # Search common config files
    for file in [".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", "pyproject.toml"]:
        p = repo_root / file
        if p.exists():
            txt = _safe_read_text(p, max_bytes=400_000).lower()
            if any(pat in txt for pat in patterns):
                return True
    return False


def _has_dead_code_tool(repo_root: Path) -> bool:
    patterns = ["vulture", "ts-prune", "knip", "unimported", "deadcode"]
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if any(p in txt for p in patterns):
                return True
    # Config files
    for file in ["pyproject.toml", "package.json"]:
        p = repo_root / file
        if p.exists():
            txt = _safe_read_text(p, max_bytes=400_000).lower()
            if any(pat in txt for pat in patterns):
                return True
    return False


def _has_dup_code_tool(repo_root: Path) -> bool:
    patterns = ["jscpd", "pmd cpd", "duplication", "sonarqube"]
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if any(p in txt for p in patterns):
                return True
    return False


def _has_module_boundary_enforcement(repo_root: Path) -> bool:
    patterns = ["import-linter", "eslint-plugin-boundaries", "nx", "bazel", "depguard", "golangci-lint", "boundaries"]
    # Only count as enforcement if there is explicit config mention of boundaries, not just a build tool.
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if "boundar" in txt or "import-linter" in txt or "depguard" in txt:
                return True
    # Config files
    for file in ["pyproject.toml", "package.json", ".golangci.yml", ".golangci.yaml", "nx.json"]:
        p = repo_root / file
        if p.exists():
            txt = _safe_read_text(p, max_bytes=400_000).lower()
            if "boundar" in txt or "import-linter" in txt or "depguard" in txt:
                return True
    return False


def _has_todo_tracking(repo_root: Path) -> bool:
    # Look for TODO scanners or enforced TODO format in CI/lint config.
    patterns = ["todo", "fixme", "todo-check", "todocheck", "todor", "forbid todo", "ticket"]
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if "todo" in txt and ("fail" in txt or "grep" in txt):
                return True
            if any(p in txt for p in ["todor", "todo-check"]):
                return True
    # eslint rules like "no-warning-comments"
    for file in [".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", "pyproject.toml"]:
        p = repo_root / file
        if p.exists():
            txt = _safe_read_text(p, max_bytes=400_000).lower()
            if "no-warning-comments" in txt or "todo" in txt and "ticket" in txt:
                return True
    return False


def _has_metrics_tracing_logging(app_root: Path) -> Tuple[bool, bool, bool]:
    return _has_metrics_lib(app_root), _has_tracing_lib(app_root), _has_logging_lib(app_root)


def _has_ci_lint_job(repo_root: Path) -> bool:
    ok, _ = _workflow_text_contains(repo_root, ["lint"])
    return ok


def _has_ci_test_job(repo_root: Path) -> bool:
    ok, _ = _workflow_text_contains(repo_root, ["test"])
    return ok


def _has_coverage_threshold(repo_root: Path) -> bool:
    # Look for --fail-under, fail_under, coverage threshold.
    wf_dir = repo_root / ".github" / "workflows"
    patterns = ["fail-under", "fail_under", "coverage", "coveralls", "codecov", "coverage threshold"]
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if ("coverage" in txt) and ("fail-under" in txt or "fail_under" in txt or "threshold" in txt):
                return True
            if "coverage" in txt and ("codecov" in txt or "coveralls" in txt):
                # best-effort: treat as coverage tracking even if threshold not explicit
                pass
    # Python coverage config
    if (repo_root / ".coveragerc").exists():
        txt = _safe_read_text(repo_root / ".coveragerc", max_bytes=200_000).lower()
        if "fail_under" in txt:
            return True
    return False


def _has_coverage_tracking(repo_root: Path) -> bool:
    wf_dir = repo_root / ".github" / "workflows"
    patterns = ["codecov", "coveralls", "coverage", "pytest --cov", "go test", "nyc", "istanbul"]
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if any(p in txt for p in patterns):
                return True
    if (repo_root / ".coveragerc").exists():
        return True
    return False


def _has_flaky_test_detection(repo_root: Path) -> bool:
    patterns = ["flaky", "quarantine", "retry", "buildpulse", "rerun", "stress"]
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if "flaky" in txt:
                return True
            if "retry" in txt and "test" in txt:
                return True
            if any(p in txt for p in ["buildpulse", "rerunfailures", "rerun-failed", "pytest-rerunfailures"]):
                return True
    return False


def _has_test_timing(repo_root: Path) -> bool:
    patterns = ["--durations", "test timing", "benchmark", "microbench", "pytest -vv", "go test -run", "jest --runinband"]
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if any(p in txt for p in patterns):
                return True
    return False


def _has_alerting(repo_root: Path) -> bool:
    # Heuristic: alert configs or integrations
    patterns = ["pagerduty", "opsgenie", "alertmanager", "prometheus alert", "alerts.yml", "alerts.yaml"]
    ok, hits = _glob_any(repo_root, ["**/alertmanager*.y*ml", "**/*alert*.y*ml", "**/prometheus/**"])
    if ok:
        return True
    # Search configs
    for p in [repo_root / "README.md", repo_root / "AGENTS.md"]:
        if p.exists():
            txt = _safe_read_text(p, max_bytes=200_000).lower()
            if any(k in txt for k in patterns):
                return True
    return False


def _has_health_checks(app_root: Path) -> bool:
    # Heuristic: look for "/health" routes or readiness endpoints
    patterns = ["healthz", "readiness", "/health", "/ready", "health_check", "liveness"]
    # Scan a few typical entry files only
    candidates = []
    for rel in ["main.go", "cmd", "src", "app", "server", "api"]:
        p = app_root / rel
        if p.exists():
            candidates.append(p)
    scanned = 0
    for c in candidates:
        if c.is_file():
            txt = _safe_read_text(c, max_bytes=200_000).lower()
            if any(k in txt for k in patterns):
                return True
            scanned += 1
        else:
            # scan limited files under dir
            for f in c.rglob("*.*"):
                if scanned > 30:
                    break
                if f.suffix.lower() not in [".py", ".ts", ".js", ".go", ".rs", ".java"]:
                    continue
                txt = _safe_read_text(f, max_bytes=50_000).lower()
                if any(k in txt for k in patterns):
                    return True
                scanned += 1
    return False


def _has_local_services_setup(repo_root: Path) -> bool:
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        if (repo_root / name).exists():
            return True
    if (repo_root / "docker").exists():
        return True
    return False


def _has_env_vars_documented(repo_root: Path) -> bool:
    ok, files = _text_any(repo_root, ["README.md", "AGENTS.md"], ["env var", "environment variable", "ENV_", ".env"])
    return ok or _has_env_template(repo_root)


def _has_ci_cache(repo_root: Path) -> bool:
    # Fast feedback proxy: presence of caching in workflows.
    ok, hits = _workflow_text_contains(repo_root, ["cache"])
    return ok


def _has_unused_dep_detection(repo_root: Path) -> bool:
    patterns = ["depcheck", "knip", "pip-extra-reqs", "deptry", "go mod tidy", "cargo udeps"]
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.y*ml"):
            txt = _safe_read_text(wf, max_bytes=400_000).lower()
            if any(p in txt for p in patterns):
                return True
    # config files
    for f in ["package.json", "pyproject.toml"]:
        p = repo_root / f
        if p.exists():
            txt = _safe_read_text(p, max_bytes=400_000).lower()
            if any(pat in txt for pat in patterns):
                return True
    return False


def _has_log_scrubbing(repo_root: Path) -> bool:
    # Heuristic: redaction/scrubbing utilities or config
    patterns = ["redact", "scrub", "pii", "mask", "secrets redaction"]
    # scan docs
    ok, _ = _text_any(repo_root, ["README.md", "AGENTS.md", "SECURITY.md"], patterns)
    if ok:
        return True
    # scan code for "redact" in common places
    for d in ["src", "app", "pkg", "internal"]:
        p = repo_root / d
        if not p.exists():
            continue
        scanned = 0
        for f in p.rglob("*.*"):
            if scanned > 50:
                break
            if f.suffix.lower() not in [".py", ".ts", ".js", ".go", ".rs", ".java"]:
                continue
            txt = _safe_read_text(f, max_bytes=40_000).lower()
            if "redact" in txt or "scrub" in txt:
                return True
            scanned += 1
    return False


def _has_db_migrations(repo_root: Path) -> bool:
    # common migration directories
    for d in ["migrations", "db/migrations", "prisma/migrations", "alembic", "knexfile.js", "flyway", "liquibase"]:
        if (repo_root / d).exists():
            return True
    return False


def _has_api_schema(repo_root: Path) -> bool:
    # OpenAPI spec files
    ok, hits = _glob_any(repo_root, ["**/openapi.y*ml", "**/swagger.y*ml", "**/openapi.json", "**/swagger.json"])
    if ok:
        return True
    # For common frameworks: presence of openapi generators not reliable; keep conservative.
    return False


# ----------------------------
# Criterion definitions
# ----------------------------

# NOTE: This is intentionally verbose. The structure mirrors how humans want to read a readiness report.
CRITERIA: List[Dict[str, Any]] = [
    # Level 1 — Functional
    {
        "id": "readme",
        "title": "README present",
        "pillar": "Documentation",
        "level": 1,
        "scope": "repo",
        "weight": 5,
        "why": "Agents need a single canonical starting point to understand purpose and basic commands.",
        "remediation": "Add a README.md with: purpose, prerequisites, setup, build/test commands, and a quickstart.",
    },
    {
        "id": "gitignore",
        "title": "Git ignore is present and reasonably comprehensive",
        "pillar": "Security & Governance",
        "level": 1,
        "scope": "repo",
        "weight": 3,
        "why": "Prevents accidental commits of secrets, build artifacts, and local environment noise.",
        "remediation": "Add/update .gitignore to exclude env files, IDE metadata, caches, and build outputs.",
    },
    {
        "id": "deps_pinned",
        "title": "Dependencies are pinned (lockfiles present)",
        "pillar": "Build System",
        "level": 1,
        "scope": "app",
        "weight": 5,
        "why": "Agents need deterministic installs. Unpinned dependencies create non-reproducible failures.",
        "remediation": "Commit lockfiles (e.g., package-lock.json, pnpm-lock.yaml, poetry.lock, uv.lock, go.sum, Cargo.lock).",
    },
    {
        "id": "lint_config",
        "title": "Linter configuration exists",
        "pillar": "Style & Validation",
        "level": 1,
        "scope": "app",
        "weight": 5,
        "why": "Linters turn many bugs into immediate feedback, reducing low-signal CI loops.",
        "remediation": "Add a linter (ESLint/Biome, Ruff, golangci-lint, Clippy) and commit its config.",
    },
    {
        "id": "formatter",
        "title": "Formatter configuration exists (or language-standard formatter enforced)",
        "pillar": "Style & Validation",
        "level": 1,
        "scope": "app",
        "weight": 4,
        "why": "Formatting consistency prevents noisy diffs and reduces review friction for agent-generated changes.",
        "remediation": "Add Prettier/Biome/Black/Ruff format and ensure it runs locally and in CI.",
    },
    {
        "id": "type_check",
        "title": "Type checking exists (or compile-time typing is inherent)",
        "pillar": "Style & Validation",
        "level": 1,
        "scope": "app",
        "weight": 4,
        "why": "Type checking catches integration errors earlier than runtime tests.",
        "remediation": "Enable TS strict mode or add mypy/pyright. Ensure type checks run in CI.",
    },
    {
        "id": "unit_tests_exist",
        "title": "Unit tests exist",
        "pillar": "Testing",
        "level": 1,
        "scope": "app",
        "weight": 5,
        "why": "Unit tests are the fastest correctness signal for iterative agent work.",
        "remediation": "Add a minimal unit test suite and a standard test runner (pytest/jest/go test/etc).",
    },
    {
        "id": "unit_tests_runnable",
        "title": "Unit tests are runnable via a standard command",
        "pillar": "Testing",
        "level": 1,
        "scope": "app",
        "weight": 4,
        "why": "Agents need an obvious, repeatable command to validate behavior before committing.",
        "remediation": "Document and standardize: `npm test` / `pytest` / `go test ./...` and ensure it works locally.",
    },
    {
        "id": "build_cmd_doc",
        "title": "Build command exists and is discoverable",
        "pillar": "Build System",
        "level": 1,
        "scope": "app",
        "weight": 4,
        "why": "Agents must be able to compile/build packages without guessing.",
        "remediation": "Add a build script/target and document it in README/AGENTS (e.g., `npm run build`, `make build`).",
    },

    # Level 2 — Documented
    {
        "id": "agents_md",
        "title": "AGENTS.md exists (agent-facing development instructions)",
        "pillar": "Documentation",
        "level": 2,
        "scope": "repo",
        "weight": 5,
        "why": "Agent-facing docs remove ambiguity: setup, commands, conventions, and 'how we work here'.",
        "remediation": "Add AGENTS.md with: setup, dev loops, common tasks, repo map, and verification commands.",
    },
    {
        "id": "contributing",
        "title": "CONTRIBUTING documentation exists",
        "pillar": "Documentation",
        "level": 2,
        "scope": "repo",
        "weight": 3,
        "why": "Contribution guidance standardizes change flow and reduces back-and-forth.",
        "remediation": "Add CONTRIBUTING.md with local dev steps, testing, PR expectations, and review notes.",
    },
    {
        "id": "pre_commit_hooks",
        "title": "Pre-commit hooks exist (or equivalent local automation)",
        "pillar": "Style & Validation",
        "level": 2,
        "scope": "app",
        "weight": 4,
        "why": "Pre-commit hooks prevent agents from creating avoidable CI churn.",
        "remediation": "Add pre-commit (Python) or Husky/lint-staged (Node) or equivalent git hook tooling.",
    },
    {
        "id": "large_file_detection",
        "title": "Large-file detection exists",
        "pillar": "Style & Validation",
        "level": 2,
        "scope": "repo",
        "weight": 2,
        "why": "Prevents accidental commits of huge binaries that break agent loops and CI performance.",
        "remediation": "Add pre-commit large-file hooks and/or Git LFS policies via .gitattributes.",
    },
    {
        "id": "ci_configured",
        "title": "CI is configured",
        "pillar": "Build System",
        "level": 2,
        "scope": "repo",
        "weight": 5,
        "why": "Agents need a consistent verification pipeline that mirrors production expectations.",
        "remediation": "Add CI workflows to run lint/typecheck/tests on PRs.",
    },
    {
        "id": "ci_lint_job",
        "title": "CI runs linting/validation",
        "pillar": "Style & Validation",
        "level": 2,
        "scope": "repo",
        "weight": 3,
        "why": "Enforcing validation in CI prevents drift and makes agent output predictable.",
        "remediation": "Add a lint job to CI (e.g., `ruff check`, `eslint`, `golangci-lint`).",
    },
    {
        "id": "ci_test_job",
        "title": "CI runs tests",
        "pillar": "Testing",
        "level": 2,
        "scope": "repo",
        "weight": 4,
        "why": "Agents rely on CI as a backstop and as evidence of correctness.",
        "remediation": "Add a test job that runs the standard local test command across supported environments.",
    },
    {
        "id": "codeowners",
        "title": "CODEOWNERS exists",
        "pillar": "Security & Governance",
        "level": 2,
        "scope": "repo",
        "weight": 4,
        "why": "Clear ownership ensures critical paths get appropriate review when agents move fast.",
        "remediation": "Add CODEOWNERS in .github/ or repo root with ownership for key directories.",
    },
    {
        "id": "pr_template",
        "title": "PR template exists",
        "pillar": "Security & Governance",
        "level": 2,
        "scope": "repo",
        "weight": 2,
        "why": "A PR template helps agents include context, risk, and verification evidence consistently.",
        "remediation": "Add .github/pull_request_template.md with checklist: tests, docs, risk, rollout/rollback notes.",
    },
    {
        "id": "issue_templates",
        "title": "Issue templates exist",
        "pillar": "Security & Governance",
        "level": 2,
        "scope": "repo",
        "weight": 1,
        "why": "Structured issues reduce ambiguity and help agents pick up well-scoped work.",
        "remediation": "Add .github/ISSUE_TEMPLATE/ with templates for bug, feature, and incident followups.",
    },
    {
        "id": "devcontainer",
        "title": "Devcontainer exists",
        "pillar": "Dev Environment",
        "level": 2,
        "scope": "repo",
        "weight": 3,
        "why": "Reproducible dev environments reduce setup variance for humans and agents.",
        "remediation": "Add .devcontainer/devcontainer.json (or equivalent) with dependencies and recommended extensions.",
    },
    {
        "id": "env_template",
        "title": "Environment template exists (.env.example)",
        "pillar": "Dev Environment",
        "level": 2,
        "scope": "repo",
        "weight": 3,
        "why": "Agents cannot guess environment variables safely. Templates prevent trial-and-error loops.",
        "remediation": "Add .env.example documenting required variables and safe defaults (no secrets).",
    },

    # Level 3 — Standardized
    {
        "id": "integration_tests",
        "title": "Integration/E2E tests exist where applicable",
        "pillar": "Testing",
        "level": 3,
        "scope": "app",
        "weight": 4,
        "why": "Integration tests validate system behavior and reduce regressions from refactors.",
        "remediation": "Add a minimal integration/e2e suite (or document why it’s not applicable).",
    },
    {
        "id": "coverage_tracking",
        "title": "Coverage tracking exists",
        "pillar": "Testing",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Coverage signals help agents understand risk and where to add tests.",
        "remediation": "Add Codecov/Coveralls or local coverage reporting (pytest-cov, nyc, go test -cover).",
    },
    {
        "id": "coverage_threshold",
        "title": "Coverage threshold is enforced",
        "pillar": "Testing",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "A threshold prevents silent test erosion as agents make frequent edits.",
        "remediation": "Configure CI to fail if coverage drops below a defined threshold.",
    },
    {
        "id": "env_vars_documented",
        "title": "Environment variables are documented",
        "pillar": "Documentation",
        "level": 3,
        "scope": "repo",
        "weight": 3,
        "why": "Agents need explicit runtime configuration knowledge to validate behavior locally.",
        "remediation": "Document required env vars in AGENTS/README and keep .env.example updated.",
    },
    {
        "id": "docs_freshness",
        "title": "Docs appear maintained (freshness signal)",
        "pillar": "Documentation",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Stale instructions cause agents to fail repeatedly with outdated commands.",
        "remediation": "Update README/AGENTS/CONTRIBUTING when commands or architecture changes.",
    },
    {
        "id": "doc_gen_automation",
        "title": "Automated documentation generation/build exists",
        "pillar": "Documentation",
        "level": 3,
        "scope": "repo",
        "weight": 1,
        "why": "Doc automation reduces drift and makes updates cheaper for agents.",
        "remediation": "Add a docs build workflow (mkdocs/sphinx/docusaurus) or a generator step.",
    },
    {
        "id": "service_flow_docs",
        "title": "Service flow / architecture is documented (diagrams or structured docs)",
        "pillar": "Documentation",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Agents are more effective when system boundaries and flows are explicit.",
        "remediation": "Add architecture docs (mermaid/plantuml) and keep a short system map.",
    },
    {
        "id": "local_services_setup",
        "title": "Local services setup exists (e.g., docker compose) if needed",
        "pillar": "Dev Environment",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Agents need a reproducible way to run dependencies (db, cache, queues) locally.",
        "remediation": "Add docker compose or scripts to start required local dependencies.",
    },
    {
        "id": "db_migrations",
        "title": "Database migrations / schema management exists (if applicable)",
        "pillar": "Dev Environment",
        "level": 3,
        "scope": "repo",
        "weight": 1,
        "why": "Schema drift breaks agent verification and creates deployment risk.",
        "remediation": "Add migrations tooling (alembic/prisma/flyway/etc) or document schema strategy.",
    },
    {
        "id": "structured_logging",
        "title": "Structured logging is present",
        "pillar": "Observability",
        "level": 3,
        "scope": "app",
        "weight": 2,
        "why": "Structured logs accelerate debugging by making failures searchable and contextual.",
        "remediation": "Adopt structured logging (JSON) and document log fields and redaction rules.",
    },
    {
        "id": "metrics_instrumentation",
        "title": "Metrics instrumentation is present",
        "pillar": "Observability",
        "level": 3,
        "scope": "app",
        "weight": 2,
        "why": "Metrics turn behavior into measurable signals agents can reason about.",
        "remediation": "Add metrics instrumentation (Prometheus/OpenTelemetry/StatsD) and document key metrics.",
    },
    {
        "id": "tracing_instrumentation",
        "title": "Distributed tracing is present",
        "pillar": "Observability",
        "level": 3,
        "scope": "app",
        "weight": 1,
        "why": "Traces connect failures across services; agents can find root causes faster.",
        "remediation": "Instrument traces via OpenTelemetry and propagate trace/request IDs.",
    },
    {
        "id": "error_tracking",
        "title": "Error tracking is present",
        "pillar": "Observability",
        "level": 3,
        "scope": "app",
        "weight": 1,
        "why": "Error tracking provides high-signal failures and context beyond logs alone.",
        "remediation": "Add error tracking (Sentry/Bugsnag/etc) with contextual metadata.",
    },
    {
        "id": "runbooks",
        "title": "Runbooks/playbooks exist (or are linked)",
        "pillar": "Observability",
        "level": 3,
        "scope": "repo",
        "weight": 1,
        "why": "Runbooks encode operational response so agents can act safely during incidents.",
        "remediation": "Create runbooks for common failure modes and link them from README/AGENTS.",
    },
    {
        "id": "health_checks",
        "title": "Health/readiness checks exist (if deployed service)",
        "pillar": "Observability",
        "level": 3,
        "scope": "app",
        "weight": 1,
        "why": "Health endpoints enable automated validation and safe rollouts.",
        "remediation": "Add /health and /ready endpoints and test them in CI.",
    },
    {
        "id": "dependabot",
        "title": "Automated dependency updates are configured",
        "pillar": "Security & Governance",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Dependency hygiene reduces risk as agents ship more frequently.",
        "remediation": "Enable Dependabot/Renovate for dependencies and CI workflows.",
    },
    {
        "id": "sast_scanning",
        "title": "Static security scanning is configured (SAST)",
        "pillar": "Security & Governance",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Automated scanning is a scalable guardrail for accelerated change.",
        "remediation": "Add CodeQL/Semgrep scanning in CI and review findings regularly.",
    },
    {
        "id": "secret_scanning_tooling",
        "title": "Secret scanning tooling exists (repo-local detectable)",
        "pillar": "Security & Governance",
        "level": 3,
        "scope": "repo",
        "weight": 2,
        "why": "Agents should not introduce secrets; scanning catches leaks quickly.",
        "remediation": "Add gitleaks or equivalent scanning in CI and a baseline allowlist as needed.",
    },
    {
        "id": "security_policy",
        "title": "Security policy exists (SECURITY.md)",
        "pillar": "Security & Governance",
        "level": 3,
        "scope": "repo",
        "weight": 1,
        "why": "Clarifies reporting and response expectations for security issues.",
        "remediation": "Add SECURITY.md describing reporting channels and response SLAs.",
    },
    {
        "id": "log_scrubbing",
        "title": "Log redaction / scrubbing mechanisms exist (best-effort detection)",
        "pillar": "Security & Governance",
        "level": 3,
        "scope": "repo",
        "weight": 1,
        "why": "As output volume increases, preventing PII/secret leakage into logs becomes critical.",
        "remediation": "Implement redaction utilities, document sensitive fields, and enforce via lint/tests.",
    },
    {
        "id": "branch_protection",
        "title": "Branch protection / required reviews are enabled (requires repo-host metadata)",
        "pillar": "Security & Governance",
        "level": 3,
        "scope": "repo",
        "weight": 3,
        "why": "Agents move fast; review gates and protected branches prevent unsafe changes from landing unreviewed.",
        "remediation": "Enable protected branches and required reviews in repository settings.",
    },

    # Level 4 — Optimized
    {
        "id": "ci_cache",
        "title": "CI uses caching (fast feedback proxy)",
        "pillar": "Build System",
        "level": 4,
        "scope": "repo",
        "weight": 2,
        "why": "Faster CI loops increase agent throughput and reduce time-to-fix.",
        "remediation": "Add dependency caching (e.g., actions/cache) and parallelize test jobs where possible.",
    },
    {
        "id": "flaky_tests",
        "title": "Flaky test detection exists",
        "pillar": "Testing",
        "level": 4,
        "scope": "repo",
        "weight": 2,
        "why": "Flaky tests waste agent cycles and create mistrust in feedback signals.",
        "remediation": "Add retries/quarantine mechanisms and track flaky tests explicitly.",
    },
    {
        "id": "test_timing",
        "title": "Test performance tracking exists (timings/benchmarks)",
        "pillar": "Testing",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Optimizing test runtime improves agent iteration speed and CI cost.",
        "remediation": "Emit timings (pytest --durations, go test timing) and monitor regressions.",
    },
    {
        "id": "unused_deps",
        "title": "Unused dependency detection exists",
        "pillar": "Code Quality",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Unused deps increase attack surface and slow down builds and agents.",
        "remediation": "Add depcheck/knip/deptry/go mod tidy checks in CI.",
    },
    {
        "id": "complexity",
        "title": "Complexity analysis exists",
        "pillar": "Code Quality",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Complex functions are harder for agents to modify safely.",
        "remediation": "Add complexity rules/tools (eslint complexity, radon/lizard/gocyclo) and refactor hot spots.",
    },
    {
        "id": "dead_code",
        "title": "Dead code detection exists",
        "pillar": "Code Quality",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Dead code confuses agents and increases hallucinated changes to unused paths.",
        "remediation": "Add vulture/ts-prune/knip or equivalent and remove unused code.",
    },
    {
        "id": "dup_code",
        "title": "Duplicate code detection exists",
        "pillar": "Code Quality",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Duplicate logic multiplies maintenance cost and increases inconsistency risk.",
        "remediation": "Add jscpd/CPD/Sonar-style duplication checks and refactor shared utilities.",
    },
    {
        "id": "module_boundaries",
        "title": "Module boundary enforcement exists (architectural constraints)",
        "pillar": "Code Quality",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Explicit boundaries prevent agents from making changes that violate architecture.",
        "remediation": "Add boundary enforcement (import-linter, eslint boundaries, depguard) and document modules.",
    },
    {
        "id": "todo_tracking",
        "title": "Tech debt tracking exists (TODO policy/scanner)",
        "pillar": "Code Quality",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Without guardrails, agents can accumulate TODO debt quickly.",
        "remediation": "Enforce TODO format (with ticket) and add CI scanners for TODO/FIXME.",
    },
    {
        "id": "alerting",
        "title": "Alerting signals/config exist",
        "pillar": "Observability",
        "level": 4,
        "scope": "repo",
        "weight": 1,
        "why": "Alerting closes the loop: agents can detect regressions and verify safe operation.",
        "remediation": "Add alert rules and document alert routing and on-call expectations.",
    },

    # Level 5 — Autonomous (rare; best-effort local signals)
    {
        "id": "agent_workflows_present",
        "title": "Agent workflows exist (repeatable automation in-repo)",
        "pillar": "Build System",
        "level": 5,
        "scope": "repo",
        "weight": 1,
        "why": "Level 5 requires repeatable automation and self-serve maintenance routines.",
        "remediation": "Add standardized automation workflows for recurring maintenance tasks.",
    },
]


def evaluate_criterion_repo(repo_root: Path, apps: List[App], crit_id: str) -> List[EvalUnitResult]:
    # Repo-scoped checks
    if crit_id == "readme":
        ok, hits = _exists_any(repo_root, ["README.md", "README.rst", "README.txt", "README"])
        if ok:
            return [_make_unit("repo", "pass", "Found README.", hits)]
        return [_make_unit("repo", "fail", "No README found.", [])]

    if crit_id == "gitignore":
        if _gitignore_comprehensive(repo_root):
            return [_make_unit("repo", "pass", ".gitignore exists and contains common exclusions.", [".gitignore"])]
        if (repo_root / ".gitignore").exists():
            return [_make_unit("repo", "fail", ".gitignore exists but seems minimal (missing common exclusions).", [".gitignore"])]
        return [_make_unit("repo", "fail", "No .gitignore found.", [])]

    if crit_id == "large_file_detection":
        if _has_precommit_large_file_detection(repo_root):
            evidence = []
            if (repo_root / ".gitattributes").exists():
                evidence.append(".gitattributes")
            if (repo_root / ".pre-commit-config.yaml").exists():
                evidence.append(".pre-commit-config.yaml")
            return [_make_unit("repo", "pass", "Large-file detection appears configured.", evidence)]
        return [_make_unit("repo", "fail", "No evidence of large-file detection hooks or LFS policy.", [])]

    if crit_id == "ci_configured":
        if _has_ci(repo_root):
            evidence = []
            if (repo_root / ".github" / "workflows").exists():
                evidence.append(".github/workflows/")
            if (repo_root / ".gitlab-ci.yml").exists():
                evidence.append(".gitlab-ci.yml")
            if (repo_root / "azure-pipelines.yml").exists():
                evidence.append("azure-pipelines.yml")
            return [_make_unit("repo", "pass", "CI configuration detected.", evidence)]
        return [_make_unit("repo", "fail", "No CI configuration detected.", [])]

    if crit_id == "ci_lint_job":
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate lint job.", [])]
        if _has_ci_lint_job(repo_root):
            return [_make_unit("repo", "pass", "CI appears to run lint/validation.", [".github/workflows/*"])]
        return [_make_unit("repo", "fail", "CI detected, but no obvious lint job found.", [".github/workflows/*"] if (repo_root / ".github/workflows").exists() else [])]

    if crit_id == "ci_test_job":
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate test job.", [])]
        if _has_ci_test_job(repo_root):
            return [_make_unit("repo", "pass", "CI appears to run tests.", [".github/workflows/*"])]
        return [_make_unit("repo", "fail", "CI detected, but no obvious test job found.", [".github/workflows/*"] if (repo_root / ".github/workflows").exists() else [])]

    if crit_id == "codeowners":
        if _has_codeowners(repo_root):
            evidence = ["CODEOWNERS"] if (repo_root / "CODEOWNERS").exists() else [".github/CODEOWNERS"]
            return [_make_unit("repo", "pass", "CODEOWNERS file found.", evidence)]
        return [_make_unit("repo", "fail", "No CODEOWNERS file found.", [])]

    if crit_id == "pr_template":
        if _has_pr_template(repo_root):
            return [_make_unit("repo", "pass", "PR template found.", [".github/pull_request_template.md"])]
        return [_make_unit("repo", "fail", "No PR template found.", [])]

    if crit_id == "issue_templates":
        if _has_issue_templates(repo_root):
            return [_make_unit("repo", "pass", "Issue templates directory found.", [".github/ISSUE_TEMPLATE/"])]
        return [_make_unit("repo", "fail", "No issue templates directory found.", [])]

    if crit_id == "devcontainer":
        if _has_devcontainer(repo_root):
            return [_make_unit("repo", "pass", "Devcontainer configuration found.", [".devcontainer/devcontainer.json"])]
        return [_make_unit("repo", "fail", "No devcontainer configuration found.", [])]

    if crit_id == "env_template":
        if _has_env_template(repo_root):
            return [_make_unit("repo", "pass", "Environment template found.", [".env.example"])]
        return [_make_unit("repo", "fail", "No .env.example (or equivalent) found.", [])]

    if crit_id == "agents_md":
        if (repo_root / "AGENTS.md").exists():
            return [_make_unit("repo", "pass", "AGENTS.md found at repo root.", ["AGENTS.md"])]
        return [_make_unit("repo", "fail", "No AGENTS.md found at repo root.", [])]

    if crit_id == "contributing":
        if (repo_root / "CONTRIBUTING.md").exists():
            return [_make_unit("repo", "pass", "CONTRIBUTING.md found.", ["CONTRIBUTING.md"])]
        return [_make_unit("repo", "fail", "No CONTRIBUTING.md found.", [])]

    if crit_id == "coverage_tracking":
        if _has_coverage_tracking(repo_root):
            return [_make_unit("repo", "pass", "Coverage tracking evidence found (CI/config).", [".github/workflows/*", ".coveragerc"])]
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; coverage tracking unclear.", [])]
        return [_make_unit("repo", "fail", "No coverage tracking evidence found in CI/config.", [".github/workflows/*"] if (repo_root / ".github/workflows").exists() else [])]

    if crit_id == "coverage_threshold":
        if _has_coverage_threshold(repo_root):
            return [_make_unit("repo", "pass", "Coverage threshold evidence found.", [".github/workflows/*", ".coveragerc"])]
        if _has_coverage_tracking(repo_root):
            return [_make_unit("repo", "fail", "Coverage tracking found, but no threshold evidence detected.", [".github/workflows/*", ".coveragerc"])]
        return [_make_unit("repo", "skip", "No coverage tooling detected; cannot evaluate threshold.", [])]

    if crit_id == "env_vars_documented":
        if _has_env_vars_documented(repo_root):
            return [_make_unit("repo", "pass", "Environment variables appear documented (or template exists).", ["README.md", "AGENTS.md", ".env.example"])]
        return [_make_unit("repo", "fail", "No clear evidence of environment variable documentation or templates.", ["README.md", "AGENTS.md"])]

    if crit_id == "docs_freshness":
        ok, note = _documentation_freshness(repo_root, days=180)
        if ok:
            return [_make_unit("repo", "pass", note, ["README.md", "AGENTS.md", "CONTRIBUTING.md"])]
        # If we can't evaluate, treat as skip rather than fail.
        if "git history unavailable" in note.lower():
            return [_make_unit("repo", "skip", note, [])]
        return [_make_unit("repo", "fail", note, ["README.md", "AGENTS.md", "CONTRIBUTING.md"])]

    if crit_id == "doc_gen_automation":
        if _has_doc_gen_automation(repo_root):
            return [_make_unit("repo", "pass", "Docs automation signals found in workflows.", [".github/workflows/*"])]
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate docs automation.", [])]
        return [_make_unit("repo", "fail", "No obvious docs generation/build automation found.", [".github/workflows/*"])]

    if crit_id == "service_flow_docs":
        if _has_diagrams(repo_root):
            return [_make_unit("repo", "pass", "Architecture/service flow documentation signals found.", ["docs/", "**/*.mermaid", "**/*.puml"])]
        return [_make_unit("repo", "fail", "No clear architecture/service-flow documentation signals found.", ["docs/", "README.md", "AGENTS.md"])]

    if crit_id == "local_services_setup":
        if _has_local_services_setup(repo_root):
            return [_make_unit("repo", "pass", "Local services setup detected (compose/docker).", ["docker-compose.yml", "compose.yml", "docker/"])]
        return [_make_unit("repo", "skip", "No local services setup detected; may be unnecessary for this repo.", [])]

    if crit_id == "db_migrations":
        if _has_db_migrations(repo_root):
            return [_make_unit("repo", "pass", "Database migration/schema tooling detected.", ["migrations/", "alembic/", "prisma/"])]
        return [_make_unit("repo", "skip", "No migrations detected; may be inapplicable (no database).", [])]

    if crit_id == "dependabot":
        if _has_dependabot_or_renovate(repo_root):
            evidence = []
            if (repo_root / ".github" / "dependabot.yml").exists():
                evidence.append(".github/dependabot.yml")
            if (repo_root / "renovate.json").exists():
                evidence.append("renovate.json")
            return [_make_unit("repo", "pass", "Automated dependency update config found.", evidence)]
        return [_make_unit("repo", "fail", "No Dependabot/Renovate configuration detected.", [])]

    if crit_id == "sast_scanning":
        if _has_sast_config(repo_root):
            return [_make_unit("repo", "pass", "Static scanning configuration detected.", [".github/workflows/codeql.yml", ".semgrep.yml"])]
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate scanning.", [])]
        return [_make_unit("repo", "fail", "No static security scanning config detected.", [".github/workflows/*"] if (repo_root / ".github/workflows").exists() else [])]

    if crit_id == "secret_scanning_tooling":
        if _has_secret_scanning_tooling(repo_root):
            return [_make_unit("repo", "pass", "Secret scanning tooling/config detected.", [".gitleaks.toml", ".github/workflows/*"])]
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; secret scanning unclear.", [])]
        return [_make_unit("repo", "fail", "No repo-local detectable secret scanning tooling found.", [".github/workflows/*"])]

    if crit_id == "security_policy":
        if _has_security_policy(repo_root):
            return [_make_unit("repo", "pass", "SECURITY.md found.", ["SECURITY.md"])]
        return [_make_unit("repo", "fail", "No SECURITY.md found.", [])]

    if crit_id == "log_scrubbing":
        if _has_log_scrubbing(repo_root):
            return [_make_unit("repo", "pass", "Log scrubbing/redaction signals found (best-effort).", ["AGENTS.md", "SECURITY.md", "src/*"])]
        return [_make_unit("repo", "fail", "No obvious log scrubbing/redaction signals found (best-effort).", [])]

    if crit_id == "branch_protection":
        # Not locally determinable in general.
        return [_make_unit("repo", "skip", "Requires repository host settings (branch protection / required reviews).", [])]

    if crit_id == "ci_cache":
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate caching.", [])]
        if _has_ci_cache(repo_root):
            return [_make_unit("repo", "pass", "Caching signals found in workflows.", [".github/workflows/*"])]
        return [_make_unit("repo", "fail", "No obvious caching signals found in workflows.", [".github/workflows/*"])]

    if crit_id == "flaky_tests":
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate flaky test detection.", [])]
        if _has_flaky_test_detection(repo_root):
            return [_make_unit("repo", "pass", "Flaky test detection signals found.", [".github/workflows/*"])]
        return [_make_unit("repo", "fail", "No flaky test detection signals found.", [".github/workflows/*"])]

    if crit_id == "test_timing":
        if not _has_ci(repo_root):
            return [_make_unit("repo", "skip", "CI not detected; cannot evaluate test timing.", [])]
        if _has_test_timing(repo_root):
            return [_make_unit("repo", "pass", "Test timing/benchmark signals found.", [".github/workflows/*"])]
        return [_make_unit("repo", "fail", "No test timing/benchmark signals found.", [".github/workflows/*"])]

    if crit_id == "unused_deps":
        if _has_unused_dep_detection(repo_root):
            return [_make_unit("repo", "pass", "Unused dependency detection signals found.", [".github/workflows/*", "package.json", "pyproject.toml"])]
        return [_make_unit("repo", "fail", "No unused dependency detection signals found.", [])]

    if crit_id == "complexity":
        if _has_complexity_tool(repo_root):
            return [_make_unit("repo", "pass", "Complexity analysis signals found.", [".github/workflows/*", ".eslintrc*", "pyproject.toml"])]
        return [_make_unit("repo", "fail", "No complexity analysis signals found.", [])]

    if crit_id == "dead_code":
        if _has_dead_code_tool(repo_root):
            return [_make_unit("repo", "pass", "Dead code detection signals found.", [".github/workflows/*", "package.json", "pyproject.toml"])]
        return [_make_unit("repo", "fail", "No dead code detection signals found.", [])]

    if crit_id == "dup_code":
        if _has_dup_code_tool(repo_root):
            return [_make_unit("repo", "pass", "Duplicate code detection signals found.", [".github/workflows/*"])]
        return [_make_unit("repo", "fail", "No duplicate code detection signals found.", [])]

    if crit_id == "module_boundaries":
        if _has_module_boundary_enforcement(repo_root):
            return [_make_unit("repo", "pass", "Module boundary enforcement signals found.", [".github/workflows/*", "pyproject.toml", ".golangci.yml"])]
        return [_make_unit("repo", "fail", "No module boundary enforcement signals found.", [])]

    if crit_id == "todo_tracking":
        if _has_todo_tracking(repo_root):
            return [_make_unit("repo", "pass", "Tech debt tracking/TODO policy signals found.", [".github/workflows/*", ".eslintrc*", "pyproject.toml"])]
        return [_make_unit("repo", "fail", "No obvious tech debt tracking/TODO policy signals found.", [])]

    if crit_id == "alerting":
        if _has_alerting(repo_root):
            return [_make_unit("repo", "pass", "Alerting configuration signals found.", ["prometheus/", "**/alert*.yml"])]
        return [_make_unit("repo", "fail", "No alerting configuration signals found.", [])]

    if crit_id == "agent_workflows_present":
        # Local signal: presence of .windsurf workflows or other automation scripts.
        if (repo_root / ".windsurf").exists():
            return [_make_unit("repo", "pass", "Found .windsurf automation assets.", [".windsurf/"])]
        # fallback: scheduled workflows
        ok, hits = _workflow_text_contains(repo_root, ["schedule"])
        if ok:
            return [_make_unit("repo", "pass", "Found scheduled automation in CI workflows.", hits)]
        return [_make_unit("repo", "fail", "No obvious in-repo automation workflows found.", [])]

    # Default: unknown criterion id
    return [_make_unit("repo", "skip", f"Unknown criterion id: {crit_id}", [])]


def evaluate_criterion_app(repo_root: Path, app: App, crit_id: str) -> EvalUnitResult:
    app_root = repo_root / app.path if app.path != "." else repo_root
    unit = app.path

    if crit_id == "deps_pinned":
        if _deps_pinned(repo_root, app_root):
            return _make_unit(unit, "pass", "Lockfile(s) detected.", [])
        return _make_unit(unit, "fail", "No lockfile detected for this app.", [])

    if crit_id == "lint_config":
        if _has_linter(app_root):
            return _make_unit(unit, "pass", "Linter config/tooling detected.", [])
        return _make_unit(unit, "fail", "No linter config/tooling detected.", [])

    if crit_id == "formatter":
        if _has_formatter(app_root):
            return _make_unit(unit, "pass", "Formatter config/tooling detected.", [])
        return _make_unit(unit, "fail", "No formatter config/tooling detected.", [])

    if crit_id == "type_check":
        if _has_typecheck(app_root):
            return _make_unit(unit, "pass", "Type checking detected (or inherent in language).", [])
        return _make_unit(unit, "fail", "No type checking signals detected.", [])

    if crit_id == "unit_tests_exist":
        if app.kind == "go":
            ok = _has_go_tests(app_root)
        elif app.kind == "python":
            ok = _has_py_tests(app_root)
        elif app.kind == "node":
            ok = _has_node_tests(app_root)
        elif app.kind == "rust":
            # heur: tests in Cargo workspace are common; presence of tests/ or #[test] is expensive to scan
            ok = (app_root / "tests").exists() or (app_root / "src").exists()
        else:
            ok = (app_root / "tests").exists()
        if ok:
            return _make_unit(unit, "pass", "Test files/directories detected.", [])
        return _make_unit(unit, "fail", "No obvious unit test signals detected.", [])

    if crit_id == "unit_tests_runnable":
        # Use language-specific heuristics
        if app.kind == "go":
            if (app_root / "go.mod").exists():
                return _make_unit(unit, "pass", "Go tests are runnable via `go test` when go.mod exists.", ["go.mod"])
            return _make_unit(unit, "skip", "No go.mod; go test command may be unclear.", [])
        if app.kind == "python":
            # pytest config or CI test job
            if _pyproject_has_tool(app_root, "pytest") or (app_root / "pytest.ini").exists() or (app_root / "tox.ini").exists():
                return _make_unit(unit, "pass", "Pytest configuration detected.", ["pyproject.toml", "pytest.ini", "tox.ini"])
            # fallback: CI test job in repo
            if _has_ci_test_job(repo_root):
                return _make_unit(unit, "pass", "Repo CI appears to run tests (best-effort).", [".github/workflows/*"])
            return _make_unit(unit, "fail", "No clear test runner configuration detected.", [])
        if app.kind == "node":
            if _package_json_has_script(app_root, "test"):
                return _make_unit(unit, "pass", "package.json defines a `test` script.", ["package.json"])
            if _has_ci_test_job(repo_root):
                return _make_unit(unit, "pass", "Repo CI appears to run tests (best-effort).", [".github/workflows/*"])
            return _make_unit(unit, "fail", "No `test` script in package.json and no clear test runner config.", [])
        # Unknown kind
        if _has_ci_test_job(repo_root):
            return _make_unit(unit, "pass", "Repo CI appears to run tests (best-effort).", [".github/workflows/*"])
        return _make_unit(unit, "skip", "App type unknown; cannot confidently determine test command.", [])

    if crit_id == "build_cmd_doc":
        if _build_cmd_documented(repo_root, app_root):
            return _make_unit(unit, "pass", "Build command appears present or documented.", ["README.md", "AGENTS.md", "package.json", "Makefile"])
        return _make_unit(unit, "fail", "No clear build command/script detected or documented.", ["README.md", "AGENTS.md", "package.json", "Makefile"])

    if crit_id == "pre_commit_hooks":
        if _has_precommit(repo_root, app_root):
            return _make_unit(unit, "pass", "Pre-commit / git hook tooling detected.", [".pre-commit-config.yaml", ".husky/", "lefthook.yml"])
        return _make_unit(unit, "fail", "No pre-commit / git hook tooling detected.", [])

    if crit_id == "integration_tests":
        if _has_integration_tests(app_root):
            return _make_unit(unit, "pass", "Integration/E2E test signals detected.", ["tests/integration", "cypress/", "playwright.config.*"])
        # Many libraries don't need integration tests; treat as skip for obvious library repos.
        if app.kind in ("python", "node") and ("library" in (app.description or "").lower()):
            return _make_unit(unit, "skip", "App appears to be a library; integration tests may be inapplicable.", [])
        # If no services setup and no web/test frameworks, skip
        return _make_unit(unit, "fail", "No integration/E2E test signals detected.", [])

    if crit_id == "structured_logging":
        if _has_logging_lib(app_root):
            return _make_unit(unit, "pass", "Structured logging library detected (best-effort).", [])
        return _make_unit(unit, "fail", "No structured logging library detected (best-effort).", [])

    if crit_id == "metrics_instrumentation":
        if _has_metrics_lib(app_root):
            return _make_unit(unit, "pass", "Metrics/telemetry library detected (best-effort).", [])
        return _make_unit(unit, "fail", "No metrics/telemetry library detected (best-effort).", [])

    if crit_id == "tracing_instrumentation":
        if _has_tracing_lib(app_root):
            return _make_unit(unit, "pass", "Tracing library detected (best-effort).", [])
        return _make_unit(unit, "fail", "No tracing library detected (best-effort).", [])

    if crit_id == "error_tracking":
        if _has_error_tracking(app_root):
            return _make_unit(unit, "pass", "Error tracking signals detected (best-effort).", [])
        return _make_unit(unit, "fail", "No error tracking signals detected (best-effort).", [])

    if crit_id == "health_checks":
        # Many libraries don't have health checks.
        if app.kind in ("python", "node") and ("library" in (app.description or "").lower()):
            return _make_unit(unit, "skip", "App appears to be a library; health checks are inapplicable.", [])
        if _has_health_checks(app_root):
            return _make_unit(unit, "pass", "Health/readiness signals detected (best-effort).", [])
        return _make_unit(unit, "skip", "No health-check signals detected; may be inapplicable for non-service repos.", [])

    # Default: unknown criterion for app scope
    return _make_unit(unit, "skip", f"Unknown criterion id: {crit_id}", [])


def evaluate_all(repo_root: Path, meta: RepoMeta, criteria: List[Dict[str, Any]]) -> List[CriterionResult]:
    results: List[CriterionResult] = []
    apps = meta.discovered_apps

    for c in criteria:
        cid = c["id"]
        scope = c["scope"]
        title = c["title"]
        pillar = c["pillar"]
        level = int(c["level"])
        weight = int(c.get("weight", 1))
        why = c.get("why", "")
        remediation = c.get("remediation", "")

        unit_results: List[EvalUnitResult] = []
        if scope == "repo":
            unit_results = evaluate_criterion_repo(repo_root, apps, cid)
        elif scope == "app":
            for a in apps:
                unit_results.append(evaluate_criterion_app(repo_root, a, cid))
        else:
            unit_results = [_make_unit("repo", "skip", f"Unknown scope: {scope}", [])]

        numerator, denominator, status = _criterion_status_from_units(unit_results)

        # Aggregate reason: choose the first failing reason or first pass reason.
        reason = ""
        if status == "pass":
            for u in unit_results:
                if u.status == "pass":
                    reason = u.reason
                    break
        elif status == "fail":
            # summarize failures
            fails = [u for u in unit_results if u.status == "fail"]
            reason = fails[0].reason if fails else "One or more units failed."
        else:
            reason = unit_results[0].reason if unit_results else "Skipped."

        results.append(
            CriterionResult(
                id=cid,
                title=title,
                pillar=pillar,
                level=level,
                scope=scope,
                weight=weight,
                numerator=numerator,
                denominator=denominator,
                status=status,
                reason=reason,
                remediation=remediation,
                why=why,
                unit_results=unit_results,
            )
        )

    return results


# ----------------------------
# Scoring + recommendations
# ----------------------------

def compute_pillar_scores(criteria_results: List[CriterionResult]) -> List[Dict[str, Any]]:
    # Denominator excludes skipped
    by_pillar: Dict[str, Dict[str, int]] = {}
    for r in criteria_results:
        if r.pillar not in by_pillar:
            by_pillar[r.pillar] = {"pass": 0, "total": 0}
        if r.status == "skip":
            continue
        by_pillar[r.pillar]["total"] += 1
        if r.status == "pass":
            by_pillar[r.pillar]["pass"] += 1

    out: List[Dict[str, Any]] = []
    for pillar_name in sorted(by_pillar.keys()):
        p = by_pillar[pillar_name]
        total = p["total"]
        passed = p["pass"]
        pct = round((passed / total) * 100) if total else 0
        out.append({"pillar": pillar_name, "passed": passed, "total": total, "percent": pct})
    # Sort by percent descending
    out.sort(key=lambda x: (-x["percent"], x["pillar"]))
    return out


def compute_level_scores(criteria_results: List[CriterionResult]) -> List[Dict[str, Any]]:
    by_level: Dict[int, Dict[str, int]] = {i: {"pass": 0, "total": 0} for i in range(1, 6)}
    for r in criteria_results:
        if r.status == "skip":
            continue
        by_level[r.level]["total"] += 1
        if r.status == "pass":
            by_level[r.level]["pass"] += 1

    out: List[Dict[str, Any]] = []
    for lvl in range(1, 6):
        total = by_level[lvl]["total"]
        passed = by_level[lvl]["pass"]
        pct = round((passed / total) * 100) if total else 0
        out.append({"level": lvl, "passed": passed, "total": total, "percent": pct})
    return out


def compute_overall_pass_rate(criteria_results: List[CriterionResult]) -> Dict[str, Any]:
    total = sum(1 for r in criteria_results if r.status != "skip")
    passed = sum(1 for r in criteria_results if r.status == "pass")
    pct = round((passed / total) * 100) if total else 0
    return {"passed": passed, "total": total, "percent": pct}


def compute_level_achieved(level_scores: List[Dict[str, Any]]) -> int:
    # Gated progression: pass >=80% of previous level unlocks next level.
    # Always at least Level 1.
    scores = {ls["level"]: ls for ls in level_scores}
    achieved = 1
    # If Level 1 >= 80 => unlock Level 2, etc.
    for prev in [1, 2, 3, 4]:
        prev_total = scores[prev]["total"]
        prev_pct = scores[prev]["percent"]
        if prev_total == 0:
            # If there are no criteria at a level, don't auto-unlock; keep conservative.
            break
        if prev_pct >= 80:
            achieved = prev + 1
        else:
            break
    return achieved


def pick_strengths(pillar_scores: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
    return pillar_scores[:top_n]


def pick_opportunities(criteria_results: List[CriterionResult], top_n: int = 3) -> List[CriterionResult]:
    failing = [r for r in criteria_results if r.status == "fail"]
    failing.sort(key=lambda r: (-r.weight, r.level, r.pillar, r.id))
    return failing[:top_n]


def pick_action_items(criteria_results: List[CriterionResult], level_achieved: int, top_n: int = 3) -> List[Dict[str, Any]]:
    """Pick the highest-leverage action items to unlock the *next* maturity level.

    Readiness progression is gated: to unlock level N+1, the repo must reach ≥80%
    on level N criteria. Therefore the most useful action items focus on the
    current *blocking* level (the achieved level) rather than the next level.
    """

    if level_achieved >= 5:
        return []

    blocking_level = level_achieved
    candidates = [r for r in criteria_results if r.level == blocking_level and r.status == "fail"]

    # Prefer higher weight first (and keep output stable)
    candidates.sort(key=lambda r: (-r.weight, r.pillar, r.id))
    items: List[Dict[str, Any]] = []
    for r in candidates[:top_n]:
        items.append(
            {
                "criterion_id": r.id,
                "title": r.title,
                "pillar": r.pillar,
                "why": r.why,
                "remediation": r.remediation,
            }
        )
    return items


# ----------------------------
# Report rendering
# ----------------------------

def _pct_bar(pct: int, width: int = 22) -> str:
    filled = int(round((pct / 100) * width))
    return "█" * filled + "░" * (width - filled)


def render_markdown(meta: RepoMeta, overall: Dict[str, Any], level_scores: List[Dict[str, Any]], pillar_scores: List[Dict[str, Any]], strengths: List[Dict[str, Any]], opportunities: List[CriterionResult], action_items: List[Dict[str, Any]], criteria_results: List[CriterionResult]) -> str:
    org = meta.org_name or "Risk Tech"
    lines: List[str] = []
    lines.append(f"# {org} – Agent Readiness Report")
    lines.append("")
    lines.append(f"**Repository:** `{meta.repo_name}`")
    if meta.description:
        lines.append(f"**Description:** {meta.description}")
    lines.append(f"**Run ID:** `{meta.run_id}`")
    lines.append(f"**Generated:** {meta.generated_at}")
    if meta.commit_sha:
        lines.append(f"**Commit:** `{meta.commit_sha[:12]}`")
    if meta.detected_languages:
        lines.append(f"**Languages:** {', '.join(meta.detected_languages)}")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    level_achieved = compute_level_achieved(level_scores)
    blocking_level = level_achieved if level_achieved < 5 else 5
    target_level = min(blocking_level + 1, 5)
    lines.append(f"- **Level achieved:** **{level_achieved} / 5**")
    if level_achieved < 5:
        lines.append(f"- **Progression target:** unlock **Level {target_level}** by reaching **≥80%** on **Level {blocking_level}** criteria")
    lines.append(f"- **Overall pass rate:** **{overall['percent']}%** ({overall['passed']}/{overall['total']})")
    lines.append("")
    if strengths:
        lines.append("**Top strengths (pillars):**")
        for s in strengths:
            lines.append(f"- {s['pillar']}: **{s['percent']}%** ({s['passed']}/{s['total']})")
        lines.append("")
    if opportunities:
        lines.append("**Top opportunities (highest impact gaps):**")
        for r in opportunities:
            denom = r.denominator
            score = f"{r.numerator}/{denom}" if denom else "—"
            lines.append(f"- `{r.id}` – {r.title} ({score}) — {r.reason}")
        lines.append("")
    if action_items:
        lines.append("**Action items to reach the next level:**")
        for a in action_items:
            lines.append(f"- **{a['title']}** ({a['pillar']}) — {a['remediation']}")
        lines.append("")
    lines.append("## Maturity levels")
    lines.append("")
    lines.append("| Level | Name | Pass rate | Criteria |")
    lines.append("|---:|---|---:|---:|")
    for ls in level_scores:
        lvl_meta = next((l for l in LEVELS if l["level"] == ls["level"]), None)
        nm = lvl_meta["name"] if lvl_meta else f"Level {ls['level']}"
        lines.append(f"| {ls['level']} | {nm} | {ls['percent']}% | {ls['passed']}/{ls['total']} |")
    lines.append("")
    lines.append("## Pillars")
    lines.append("")
    lines.append("| Pillar | Pass rate | Criteria |")
    lines.append("|---|---:|---:|")
    for ps in pillar_scores:
        lines.append(f"| {ps['pillar']} | {ps['percent']}% | {ps['passed']}/{ps['total']} |")
    lines.append("")
    lines.append("## Applications discovered")
    lines.append("")
    for a in meta.discovered_apps:
        p = a.path
        nm = a.name
        kind = a.kind
        desc = f" — {a.description}" if a.description else ""
        lines.append(f"- `{p}` ({kind}) **{nm}**{desc}")
    lines.append("")
    lines.append("## Detailed criteria")
    lines.append("")
    # Group by pillar, then by level
    grouped: Dict[str, List[CriterionResult]] = {}
    for r in criteria_results:
        grouped.setdefault(r.pillar, []).append(r)
    for pillar in sorted(grouped.keys()):
        lines.append(f"### {pillar}")
        lines.append("")
        pillar_items = grouped[pillar]
        pillar_items.sort(key=lambda r: (r.level, r.id))
        for r in pillar_items:
            if r.status == "skip":
                icon = "—"
                score = "—"
            else:
                icon = "✓" if r.status == "pass" else "✗"
                score = f"{r.numerator}/{r.denominator}"
            lines.append(f"- {icon} `{r.id}` **L{r.level}** ({score}) — {r.title}")
            if r.status != "pass":
                lines.append(f"  - **Why it matters:** {r.why}")
                lines.append(f"  - **Recommendation:** {r.remediation}")
            if r.reason:
                lines.append(f"  - **Evidence:** {r.reason}")
        lines.append("")
    return "\n".join(lines)


def render_html(meta: RepoMeta, overall: Dict[str, Any], level_scores: List[Dict[str, Any]], pillar_scores: List[Dict[str, Any]], strengths: List[Dict[str, Any]], opportunities: List[CriterionResult], action_items: List[Dict[str, Any]], criteria_results: List[CriterionResult]) -> str:
    org = html.escape(meta.org_name or "Risk Tech")
    title = f"{org} – Agent Readiness Report"
    repo_name = html.escape(meta.repo_name)
    desc = html.escape(meta.description or "")
    run_id = html.escape(meta.run_id)
    generated = html.escape(meta.generated_at)
    commit = html.escape(meta.commit_sha[:12] if meta.commit_sha else "")
    languages = html.escape(", ".join(meta.detected_languages or []))

    level_achieved = compute_level_achieved(level_scores)
    blocking_level = level_achieved if level_achieved < 5 else 5
    target_level = min(blocking_level + 1, 5)

    def donut_svg(pct: int) -> str:
        r = 36
        c = 2 * math.pi * r
        offset = c * (1 - (pct / 100))
        return f"""
        <svg class=\"donut\" width=\"90\" height=\"90\" viewBox=\"0 0 100 100\" role=\"img\" aria-label=\"Overall pass rate\">
          <circle cx=\"50\" cy=\"50\" r=\"{r}\" fill=\"none\" stroke=\"rgba(255,255,255,0.12)\" stroke-width=\"10\"/>
          <circle cx=\"50\" cy=\"50\" r=\"{r}\" fill=\"none\" stroke=\"rgba(120,180,255,0.95)\" stroke-width=\"10\" stroke-linecap=\"round\"
            stroke-dasharray=\"{c:.2f}\" stroke-dashoffset=\"{offset:.2f}\" transform=\"rotate(-90 50 50)\"/>
          <text x=\"50\" y=\"54\" text-anchor=\"middle\" font-size=\"18\" font-weight=\"700\" fill=\"var(--text)\">{pct}%</text>
        </svg>
        """

    def radar_svg(labels: List[str], values: List[int]) -> str:
        # Inline SVG radar chart (single-file report; no external assets)
        size = 260
        cx = cy = size // 2
        radius = 90
        n = max(1, len(values))

        def pt(i: int, r: float) -> Tuple[float, float]:
            ang = (-math.pi / 2.0) + (2 * math.pi * (i / n))
            return (cx + r * math.cos(ang), cy + r * math.sin(ang))

        # Grid rings
        rings = [20, 40, 60, 80, 100]
        grid_paths: List[str] = []
        for p in rings:
            rr = radius * (p / 100)
            pts = [pt(i, rr) for i in range(n)]
            grid_paths.append(" ".join([f"{x:.1f},{y:.1f}" for x, y in pts]))

        # Axes
        axes = []
        for i in range(n):
            x, y = pt(i, radius)
            axes.append(f"<line x1=\"{cx}\" y1=\"{cy}\" x2=\"{x:.1f}\" y2=\"{y:.1f}\"/>")

        # Data polygon
        data_pts = [pt(i, radius * (max(0, min(100, values[i])) / 100)) for i in range(n)]
        data_path = " ".join([f"{x:.1f},{y:.1f}" for x, y in data_pts])

        # Labels
        label_elems = []
        for i, lbl in enumerate(labels):
            x, y = pt(i, radius + 18)
            anchor = "middle"
            if x < cx - 10:
                anchor = "end"
            elif x > cx + 10:
                anchor = "start"
            short = lbl
            label_elems.append(f"<text x=\"{x:.1f}\" y=\"{y:.1f}\" text-anchor=\"{anchor}\">{html.escape(short)}</text>")

        return f"""
        <svg class=\"radar\" width=\"{size}\" height=\"{size}\" viewBox=\"0 0 {size} {size}\" role=\"img\" aria-label=\"Pillar pass rate radar chart\">
          <g class=\"grid\">
            {''.join([f'<polygon points="{p}"/>' for p in grid_paths])}
            {''.join(axes)}
            <polygon class=\"data\" points=\"{data_path}\"/>
          </g>
          <g class=\"labels\">
            {''.join(label_elems)}
          </g>
        </svg>
        """

    def pill(pct: int) -> str:
        return f'<span class="pill">{pct}%</span>'

    # Group criteria by pillar
    grouped: Dict[str, List[CriterionResult]] = {}
    for r in criteria_results:
        grouped.setdefault(r.pillar, []).append(r)
    for k in grouped:
        grouped[k].sort(key=lambda r: (r.level, r.id))

    css = """
    :root {
      --bg: #0b1020;
      --card: #121a33;
      --muted: #a9b4d0;
      --text: #eef2ff;
      --good: #39d98a;
      --bad: #ff5c7a;
      --skip: #6b7aa7;
      --line: rgba(255,255,255,0.08);
      --pill: rgba(255,255,255,0.10);
      --shadow: 0 8px 30px rgba(0,0,0,0.35);
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--sans);
      background: radial-gradient(1200px 600px at 10% 0%, rgba(86,130,255,0.25), transparent 60%),
                  radial-gradient(900px 500px at 80% 10%, rgba(255,92,122,0.18), transparent 55%),
                  var(--bg);
      color: var(--text);
    }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 28px 18px 60px; }
    header h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: 0.2px; }
    header .meta { color: var(--muted); font-size: 13px; line-height: 1.5; }
    code, .mono { font-family: var(--mono); }
    .grid { display: grid; grid-template-columns: 1fr; gap: 14px; margin-top: 16px; }
    @media (min-width: 920px) {
      .grid.two { grid-template-columns: 1.15fr 0.85fr; }
      .grid.three { grid-template-columns: repeat(3, 1fr); }
    }
    .card {
      background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px 14px;
      box-shadow: var(--shadow);
    }
    .card h2 { margin: 0 0 10px; font-size: 15px; letter-spacing: 0.2px; }
    .kpi { display: flex; align-items: center; gap: 12px; }
    .kpi .big { font-size: 34px; font-weight: 700; }
    .kpi .sub { color: var(--muted); font-size: 13px; }
    .donut { display: block; }
    .radar { display: block; width: 100%; max-width: 320px; margin: 0 auto; }
    .radar .grid polygon { fill: none; stroke: var(--line); stroke-width: 1; }
    .radar .grid line { stroke: var(--line); stroke-width: 1; }
    .radar .grid polygon.data { fill: rgba(120,180,255,0.20); stroke: rgba(120,180,255,0.95); stroke-width: 2; }
    .radar .labels text { fill: var(--muted); font-size: 10px; }
    .row { display: flex; justify-content: space-between; gap: 10px; align-items: center; margin: 8px 0; }
    .bar { height: 10px; width: 100%; background: rgba(255,255,255,0.08); border-radius: 999px; overflow: hidden; }
    .bar > i { display:block; height: 100%; background: rgba(120,180,255,0.9); width: 0%; }
    .pill { display: inline-block; padding: 3px 8px; border-radius: 999px; background: var(--pill); font-size: 12px; color: var(--text); }
    .list { margin: 0; padding-left: 18px; color: var(--text); }
    .muted { color: var(--muted); }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid var(--line); padding: 8px 6px; font-size: 13px; }
    th { text-align: left; color: var(--muted); font-weight: 600; }
    .status { font-weight: 700; font-family: var(--mono); }
    .pass { color: var(--good); }
    .fail { color: var(--bad); }
    .skip { color: var(--skip); }
    details { border-top: 1px solid var(--line); padding-top: 8px; margin-top: 8px; }
    summary { cursor: pointer; font-weight: 600; }
    .criterion { margin-top: 10px; border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: rgba(0,0,0,0.12); }
    .criterion .hdr { display:flex; justify-content: space-between; gap: 10px; align-items: baseline; }
    .criterion .hdr .id { font-family: var(--mono); font-size: 12px; color: var(--muted); }
    .criterion .hdr .title { font-weight: 650; }
    .criterion .body { margin-top: 6px; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .chips { display:flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
    .chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.08); border: 1px solid var(--line); color: var(--muted); }
    footer { margin-top: 26px; color: var(--muted); font-size: 12px; }
    """

    # Build sections
    lvl_rows = ""
    for ls in level_scores:
        lvl_meta = next((l for l in LEVELS if l["level"] == ls["level"]), None)
        nm = html.escape(lvl_meta["name"] if lvl_meta else f"Level {ls['level']}")
        pct = int(ls["percent"])
        bar = f'<div class="bar"><i style="width:{pct}%"></i></div>'
        achieved_mark = " ✓" if ls["level"] <= level_achieved else ""
        lvl_rows += f"""
          <tr>
            <td><span class="mono">L{ls['level']}</span></td>
            <td>{nm}{achieved_mark}</td>
            <td style="width:55%">{bar}</td>
            <td style="text-align:right">{pct}%</td>
          </tr>
        """

    pillar_rows = ""
    for ps in pillar_scores:
        pct = int(ps["percent"])
        bar = f'<div class="bar"><i style="width:{pct}%"></i></div>'
        pillar_rows += f"""
          <tr>
            <td>{html.escape(ps['pillar'])}</td>
            <td style="width:55%">{bar}</td>
            <td style="text-align:right">{pct}%</td>
          </tr>
        """

    # Fixed-order pillar radar (stable across runs)
    pillar_order = [p["name"] for p in PILLARS]
    pillar_map = {ps["pillar"]: int(ps["percent"]) for ps in pillar_scores}
    pillar_values = [int(pillar_map.get(n, 0)) for n in pillar_order]
    pillar_radar = radar_svg(pillar_order, pillar_values)

    strengths_html = ""
    if strengths:
        strengths_html += "<ul class='list'>"
        for s in strengths:
            strengths_html += f"<li>{html.escape(s['pillar'])} — {pill(int(s['percent']))} <span class='muted'>({s['passed']}/{s['total']})</span></li>"
        strengths_html += "</ul>"
    else:
        strengths_html = "<p class='muted'>No strengths identified.</p>"

    opp_html = ""
    if opportunities:
        opp_html += "<ul class='list'>"
        for r in opportunities:
            score = "—" if r.denominator == 0 else f"{r.numerator}/{r.denominator}"
            opp_html += f"<li><span class='mono'>{html.escape(r.id)}</span> — {html.escape(r.title)} <span class='muted'>({score})</span><br><span class='muted'>{html.escape(r.reason)}</span></li>"
        opp_html += "</ul>"
    else:
        opp_html = "<p class='muted'>No failing criteria detected.</p>"

    actions_html = ""
    if action_items:
        actions_html += "<ol class='list'>"
        for a in action_items:
            actions_html += f"<li><b>{html.escape(a['title'])}</b> <span class='muted'>({html.escape(a['pillar'])})</span><br><span class='muted'>{html.escape(a['remediation'])}</span></li>"
        actions_html += "</ol>"
    else:
        actions_html = "<p class='muted'>No action items (Level 5 achieved or no next-level failures).</p>"

    apps_html = "<ul class='list'>"
    for a in meta.discovered_apps:
        apps_html += f"<li><span class='mono'>{html.escape(a.path)}</span> <span class='chip'>{html.escape(a.kind)}</span> <b>{html.escape(a.name)}</b> <span class='muted'>{html.escape(a.description or '')}</span></li>"
    apps_html += "</ul>"

    # Detailed criteria cards
    detailed_html = ""
    for pillar in sorted(grouped.keys()):
        detailed_html += f"<details open><summary>{html.escape(pillar)}</summary>"
        for r in grouped[pillar]:
            if r.status == "pass":
                status_cls = "pass"
                status_txt = "PASS"
                score = f"{r.numerator}/{r.denominator}" if r.denominator else "—"
            elif r.status == "fail":
                status_cls = "fail"
                status_txt = "FAIL"
                score = f"{r.numerator}/{r.denominator}" if r.denominator else "—"
            else:
                status_cls = "skip"
                status_txt = "SKIP"
                score = "—"

            chips = f"""
              <div class="chips">
                <span class="chip">L{r.level}</span>
                <span class="chip">{html.escape(r.scope)}</span>
                <span class="chip">{html.escape(score)}</span>
              </div>
            """
            body = ""
            if r.status != "pass":
                body = f"""
                  <div class="body">
                    <div><b>Why it matters:</b> {html.escape(r.why)}</div>
                    <div><b>Recommendation:</b> {html.escape(r.remediation)}</div>
                    <div><b>Evidence:</b> {html.escape(r.reason)}</div>
                  </div>
                """
            else:
                body = f"<div class='body'><b>Evidence:</b> {html.escape(r.reason)}</div>"

            detailed_html += f"""
              <div class="criterion">
                <div class="hdr">
                  <div>
                    <div class="id">{html.escape(r.id)}</div>
                    <div class="title">{html.escape(r.title)}</div>
                  </div>
                  <div class="status {status_cls}">{status_txt}</div>
                </div>
                {chips}
                {body}
              </div>
            """
        detailed_html += "</details>"

    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>{title}</h1>
      <div class="meta">
        <div><b>Repository:</b> <code>{repo_name}</code></div>
        {f"<div><b>Description:</b> {desc}</div>" if desc else ""}
        <div><b>Run:</b> <code>{run_id}</code> · <b>Generated:</b> {generated}</div>
        {f"<div><b>Commit:</b> <code>{commit}</code></div>" if commit else ""}
        {f"<div><b>Languages:</b> {languages}</div>" if languages else ""}
      </div>
    </header>

    <div class="grid two">
      <div class="card">
        <h2>Executive summary</h2>
        <div class="kpi">
          {donut_svg(int(overall['percent']))}
          <div>
            <div class="big">L{level_achieved}</div>
            <div class="sub">Level achieved (out of 5) · Overall pass rate <b>{overall['percent']}%</b> ({overall['passed']}/{overall['total']})</div>
            {f'<div class="muted" style="margin-top:6px">Target: unlock <b>L{target_level}</b> by reaching <b>≥80%</b> on <b>L{blocking_level}</b> criteria</div>' if level_achieved < 5 else ''}
          </div>
        </div>
        <div style="margin-top: 12px" class="grid three">
          <div class="card" style="box-shadow:none; padding: 12px; background: rgba(0,0,0,0.12);">
            <h2 style="margin-bottom:8px">Strengths</h2>
            {strengths_html}
          </div>
          <div class="card" style="box-shadow:none; padding: 12px; background: rgba(0,0,0,0.12);">
            <h2 style="margin-bottom:8px">Opportunities</h2>
            {opp_html}
          </div>
          <div class="card" style="box-shadow:none; padding: 12px; background: rgba(0,0,0,0.12);">
            <h2 style="margin-bottom:8px">Action items</h2>
            {actions_html}
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Maturity levels</h2>
        <table>
          <thead><tr><th>Level</th><th>Name</th><th>Progress</th><th style="text-align:right">Pass</th></tr></thead>
          <tbody>
            {lvl_rows}
          </tbody>
        </table>

        <div style="height:12px"></div>

        <h2>Pillars</h2>
        <table>
          <thead><tr><th>Pillar</th><th>Progress</th><th style="text-align:right">Pass</th></tr></thead>
          <tbody>
            {pillar_rows}
          </tbody>
        </table>

        <div style="height:12px"></div>
        <h2>Pillar radar</h2>
        <p class="muted" style="margin-top:0">Pass rate by pillar (skipped criteria excluded from denominators).</p>
        {pillar_radar}
      </div>
    </div>

    <div class="grid two">
      <div class="card">
        <h2>Applications discovered</h2>
        <p class="muted">For monorepos, app-scoped criteria are evaluated per application and shown as <code>n/d</code>.</p>
        {apps_html}
      </div>
      <div class="card">
        <h2>How to read this</h2>
        <p class="muted">
          This report evaluates readiness across eight pillars and five maturity levels.
          Criteria are binary (pass/fail) with <b>Skipped</b> when a signal requires repo-host metadata
          or is clearly inapplicable.
        </p>
        <p class="muted">
          Improving the environment compounds: faster feedback → better agent output → more automation capacity → even faster feedback.
        </p>
      </div>
    </div>

    <div class="card">
      <h2>Detailed criteria</h2>
      {detailed_html}
    </div>

    <footer>
      Generated by <b>Risk Tech – rt-agent-readiness</b>. Output is deterministic where repo-local evidence exists.
    </footer>
  </div>
</body>
</html>
"""
    return html_doc


# ----------------------------
# Main
# ----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", help="Path to repository root (default: .)")
    ap.add_argument("--out", default="artifacts/rt-agent-readiness", help="Output base directory")
    ap.add_argument("--run-id", default="", help="Optional run id. If empty, a timestamp-based id is generated.")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_base = Path(args.out).resolve()
    cfg = load_optional_config(repo_root)
    org_name = str(cfg.get("org_name") or "Risk Tech")

    run_id = args.run_id.strip() or (_dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + _short_hash(str(repo_root)))
    run_dir = out_base / run_id
    inputs_dir = run_dir / "inputs"
    outputs_dir = run_dir / "outputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Git metadata
    commit_sha = ""
    default_branch = ""
    rc, out = _run_git(repo_root, ["rev-parse", "HEAD"])
    if rc == 0:
        commit_sha = out.strip()
    rc, out = _run_git(repo_root, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if rc == 0 and out:
        default_branch = out.split("/")[-1].strip()

    apps = discover_apps(repo_root, cfg)
    langs = detect_languages(apps)
    repo_name = detect_repo_name(repo_root)
    desc = detect_repo_description(repo_root)

    meta = RepoMeta(
        repo_root=str(repo_root),
        repo_name=repo_name,
        description=desc,
        commit_sha=commit_sha,
        default_branch=default_branch,
        detected_languages=langs,
        discovered_apps=apps,
        run_id=run_id,
        generated_at=_utc_now_iso(),
        org_name=org_name,
    )

    # Persist inputs (for audit)
    (inputs_dir / "config.json").write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    (inputs_dir / "apps.json").write_text(json.dumps([dataclasses.asdict(a) for a in apps], indent=2), encoding="utf-8")

    criteria_results = evaluate_all(repo_root, meta, CRITERIA)

    pillar_scores = compute_pillar_scores(criteria_results)
    level_scores = compute_level_scores(criteria_results)
    overall = compute_overall_pass_rate(criteria_results)
    level_achieved = compute_level_achieved(level_scores)

    strengths = pick_strengths(pillar_scores, top_n=3)
    opportunities = pick_opportunities(criteria_results, top_n=3)
    action_items = pick_action_items(criteria_results, level_achieved, top_n=3)

    readiness = {
        "framework": {
            "name": "Risk Tech – Agent Readiness",
            "version": "1.1.0",
            "pillars": PILLARS,
            "levels": LEVELS,
            "scoring": {
                "criteria_mode": "binary",
                "skip_handling": "skipped criteria excluded from denominators",
                "level_progression": "gated; passing a level unlocks the next level (80% threshold on the previous level)",
            },
        },
        "meta": dataclasses.asdict(meta),
        "scores": {
            "overall": overall,
            "level_achieved": level_achieved,
            "blocking_level": level_achieved if level_achieved < 5 else 5,
            "next_level_target": min((level_achieved if level_achieved < 5 else 5) + 1, 5),
            "levels": level_scores,
            "pillars": pillar_scores,
        },
        "highlights": {
            "strengths": strengths,
            "opportunities": [dataclasses.asdict(o) for o in opportunities],
        },
        "action_items": action_items,
        "criteria": [dataclasses.asdict(r) for r in criteria_results],
    }

    # Write JSON
    (outputs_dir / "readiness.json").write_text(json.dumps(readiness, indent=2), encoding="utf-8")

    # Write reports
    report_md = render_markdown(meta, overall, level_scores, pillar_scores, strengths, opportunities, action_items, criteria_results)
    (outputs_dir / "report.md").write_text(report_md, encoding="utf-8")

    report_html = render_html(meta, overall, level_scores, pillar_scores, strengths, opportunities, action_items, criteria_results)
    (outputs_dir / "report.html").write_text(report_html, encoding="utf-8")

    # Print run directory for workflow chaining
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
