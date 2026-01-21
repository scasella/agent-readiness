\
#!/usr/bin/env python3
"""
Validate /rt-agent-readiness outputs.

This is a lightweight gate to ensure the workflow produced the required artifacts.
(Uses only Python stdlib.)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_OUTPUTS = [
    "outputs/readiness.json",
    "outputs/report.md",
    "outputs/report.html",
]


def _fail(msg: str) -> int:
    print(f"[rt-agent-readiness][FAIL] {msg}")
    return 2


def _ok(msg: str) -> None:
    print(f"[rt-agent-readiness][OK] {msg}")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Run directory path printed by rt_agent_readiness.py")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        return _fail(f"Run dir does not exist: {run_dir}")

    # Check required outputs
    for rel in REQUIRED_OUTPUTS:
        p = run_dir / rel
        if not p.exists():
            return _fail(f"Missing required output: {rel}")
        _ok(f"Found {rel}")

    # Basic JSON shape checks
    readiness = _load_json(run_dir / "outputs/readiness.json")
    for key in ["framework", "meta", "scores", "criteria", "action_items"]:
        if key not in readiness:
            return _fail(f"readiness.json missing key: {key}")
    _ok("readiness.json contains required top-level keys")

    scores = readiness.get("scores") or {}
    for key in ["overall", "level_achieved", "levels", "pillars"]:
        if key not in scores:
            return _fail(f"readiness.json.scores missing key: {key}")
    _ok("readiness.json.scores contains required keys")

    # Ensure reports are non-empty
    for rel in ["outputs/report.md", "outputs/report.html"]:
        p = run_dir / rel
        if p.stat().st_size < 200:
            return _fail(f"{rel} is unexpectedly small; report generation may have failed.")
        _ok(f"{rel} looks non-empty")

    print("[rt-agent-readiness] Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
