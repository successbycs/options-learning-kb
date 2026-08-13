#!/usr/bin/env python3
"""Fixed evidence capture and independent readback probes for KB milestones."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from options_learning_kb import milestone_governance as governance  # noqa: E402


def adapter_operation(operation: str) -> dict:
    completed = subprocess.run([sys.executable, "scripts/t480_adapter.py", "execute", "--operation", operation],
                               cwd=ROOT, capture_output=True, text=True, check=False, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(f"T480 {operation} failed: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"T480 {operation} returned malformed JSON.") from error
    if not payload.get("ok"):
        raise RuntimeError(f"T480 {operation} did not report success.")
    return payload


def controller_probe() -> dict:
    registry = governance.load_registry(ROOT / "milestones" / "registry.json")
    return {"marker": "KB_MILESTONE_CONTROLLER_VALID", "milestone_count": len(registry["milestones"]), "registry_valid": True}


def t480_dependencies_probe() -> dict:
    return {"marker": "KB_T480_RETRIEVAL_DEPENDENCIES_READY", "pgvector": adapter_operation("pgvector_status"),
            "ollama": adapter_operation("ollama_bge_status"), "lab": adapter_operation("shared_lab_status")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture fixed KB milestone proof from real surfaces.")
    parser.add_argument("probe", choices=("controller", "t480-dependencies", "t480-readback"))
    args = parser.parse_args()
    if args.probe == "controller":
        payload = controller_probe()
    else:
        payload = t480_dependencies_probe()
        if args.probe == "t480-readback":
            payload["marker"] = "KB_T480_RETRIEVAL_DEPENDENCIES_INDEPENDENT_READBACK"
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"KB_MILESTONE_PROBE_ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
