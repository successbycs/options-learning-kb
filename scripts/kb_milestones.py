#!/usr/bin/env python3
"""Operate the Options Learning KB evidence-first delivery controller."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from uuid import uuid4
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from options_learning_kb import milestone_governance as governance  # noqa: E402

REGISTRY_PATH = ROOT / "milestones" / "registry.json"
STATE_PATH = ROOT / governance.STATE_RELATIVE_PATH


def t480_dependencies_readback(root: Path) -> dict:
    completed = subprocess.run([sys.executable, "scripts/kb_milestone_probe.py", "t480-readback"], cwd=root,
                               capture_output=True, text=True, check=False, timeout=180)
    if completed.returncode != 0:
        raise governance.GovernanceError(f"Independent T480 readback failed: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise governance.GovernanceError("Independent T480 readback was malformed.") from error
    if payload.get("marker") != "KB_T480_RETRIEVAL_DEPENDENCIES_INDEPENDENT_READBACK":
        raise governance.GovernanceError("Independent T480 readback marker is absent.")
    capture_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    path = root / governance.EVIDENCE_RELATIVE_PATH / "KB-M1" / f"{capture_id}_independent-readback.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"marker": payload["marker"], "operations": ["pgvector_status", "ollama_bge_status", "shared_lab_status"],
            "evidence_path": str(path.relative_to(root)), "evidence_sha256": governance.file_sha256(path)}


VERIFIERS = {"t480_retrieval_dependencies_readback": t480_dependencies_readback}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Evidence-first Options Learning KB delivery controller")
    result.add_argument("command", choices=("validate-registry", "status", "start", "capture", "verify", "complete", "block", "needs-fix"))
    result.add_argument("--id", help="Milestone ID")
    result.add_argument("--reason", help="Concrete reason for blocked or needs-fix")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        registry = governance.load_registry(REGISTRY_PATH)
        if args.command == "validate-registry":
            print("KB_MILESTONE_REGISTRY_VALID")
            print(f"milestones={len(registry['milestones'])}")
            return 0
        state = governance.load_state(STATE_PATH)
        if args.command == "status":
            print(json.dumps(governance.status_report(registry, state), indent=2))
            return 0
        if not args.id:
            raise governance.GovernanceError("--id is required.")
        if args.command == "start":
            governance.start_milestone(registry, state, args.id)
        elif args.command == "capture":
            evidence = governance.capture_evidence(registry, state, args.id, ROOT)
            governance.write_json(STATE_PATH, state)
            print(json.dumps(evidence, indent=2))
            return 0
        elif args.command == "verify":
            print(json.dumps(governance.verify_milestone(registry, state, args.id, ROOT, VERIFIERS), indent=2))
            return 0
        elif args.command == "complete":
            result = governance.complete_milestone(registry, state, args.id, ROOT, VERIFIERS)
            governance.write_json(STATE_PATH, state)
            print(json.dumps({"milestone_id": args.id, "status": "complete", "proof": result}, indent=2))
            return 0
        else:
            status = "blocked" if args.command == "block" else "needs_fix"
            governance.set_noncompletion(registry, state, args.id, status, args.reason or "")
        governance.write_json(STATE_PATH, state)
        print(json.dumps({"milestone_id": args.id, "status": state["milestones"][args.id]["status"]}, indent=2))
        return 0
    except governance.GovernanceError as error:
        print(f"KB_MILESTONE_GOVERNANCE_ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
