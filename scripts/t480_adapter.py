#!/usr/bin/env python3
"""Governed, read-only T480 Windows/WSL/Docker adapter for Options Learning KB.

Ported from cs-ai-lab-infra's T480 adapter. It intentionally exposes only the
fixed operation IDs published in t480/command-catalog.json; callers cannot
provide a shell command, SSH arguments, or WSL script. This first port is
read-only so it can safely review the shared T480 runtime used by the KB.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

TOOL_ID = "options_learning_kb_t480"
SSH_TARGET_ENV = "T480_SSH_TARGET"
ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG_PATH = ROOT / ".env.t480.local"
SHARED_LAB_CONFIG_PATH = Path("/home/chris/projects/cs-ai-lab-infra/.env.t480.local")
EXECUTION_LOG_PATH = ROOT / ".t480-execution.local.jsonl"
CATALOG_PATH = ROOT / "t480" / "command-catalog.json"
LAB_ROOT = "/home/chris/projects/cs-ai-lab-infra"
KB_ROOT = "/home/chris/projects/options-learning-kb"


# The command text belongs here, not in user-provided arguments. All operations
# are inspection-only; configuration, database migrations, and service starts
# remain separate explicit-approval actions in their owning repositories.
OPERATIONS: dict[str, dict[str, Any]] = {
    "health": {
        "purpose": "Inspect non-secret T480 Windows host health.",
        "command": "$ErrorActionPreference='Stop'; $os=Get-CimInstance Win32_OperatingSystem; $computer=Get-CimInstance Win32_ComputerSystem; [pscustomobject]@{hostname=$env:COMPUTERNAME;os=$os.Caption;version=$os.Version;uptime_since_utc=$os.LastBootUpTime.ToUniversalTime().ToString('o');memory_gib=[math]::Round($computer.TotalPhysicalMemory/1GB,1)} | ConvertTo-Json -Compress",
    },
    "storage": {
        "purpose": "Inspect Windows filesystem capacity without changing it.",
        "command": "$ErrorActionPreference='Stop'; Get-CimInstance Win32_LogicalDisk -Filter 'DriveType = 3' | Select-Object DeviceID,@{Name='size_gib';Expression={[math]::Round($_.Size/1GB,1)}},@{Name='free_gib';Expression={[math]::Round($_.FreeSpace/1GB,1)}} | ConvertTo-Json -Compress",
    },
    "wsl_status": {
        "purpose": "Inspect WSL installation, default version, and distributions.",
        "command": "$ErrorActionPreference='Stop'; wsl.exe --status; wsl.exe --list --verbose",
    },
    "docker_status": {
        "purpose": "Inspect Docker Engine and Compose availability in the Ubuntu WSL distribution.",
        "wsl_script": "set -euo pipefail\ndocker --version\ndocker compose version\ndocker info >/dev/null\necho docker-daemon-ok\n",
    },
    "docker_runtime_evidence": {
        "purpose": "Inspect Docker service, capacity, Engine, Compose, and daemon access.",
        "wsl_script": "set -euo pipefail\necho ---os---\nuname -a\necho ---capacity---\ndf -h /\nfree -h\necho ---docker---\nsystemctl is-active docker || true\ndocker --version\ndocker compose version\ndocker info >/dev/null\necho docker-daemon-ok\n",
    },
    "shared_lab_status": {
        "purpose": "Inspect shared cs-ai-lab Compose configuration validity and running service health.",
        "wsl_script": "set -euo pipefail\ncd " + LAB_ROOT + "\necho ---revision---\ngit rev-parse --short HEAD 2>/dev/null || true\necho ---compose-validation---\ndocker compose config --quiet\necho valid\necho ---services---\ndocker compose ps -a\necho ---network---\ndocker network inspect cs-ai-lab_internal --format '{{.Name}} driver={{.Driver}} containers={{len .Containers}}'\n",
    },
    "pgvector_status": {
        "purpose": "Inspect the existing PostgreSQL/pgvector health and extension without exposing its port or credentials.",
        "wsl_script": "set -euo pipefail\ncd " + LAB_ROOT + "\nset -a\nsource .env\nset +a\ndocker compose ps postgres\ndocker compose exec -T postgres pg_isready -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" </dev/null\ndocker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc \"SELECT current_database(), extname FROM pg_extension WHERE extname = 'vector';\" </dev/null\n",
    },
    "ollama_bge_status": {
        "purpose": "Inspect private Ollama health and confirm the local bge-m3 embedding model is installed.",
        "wsl_script": "set -euo pipefail\ncd " + LAB_ROOT + "\necho ---ollama-container---\ndocker compose --profile ollama ps ollama\nif docker compose --profile ollama ps --status running --services | grep -qx ollama; then\n  echo ---models---\n  docker compose exec -T ollama ollama list\n  docker compose exec -T ollama ollama list | grep -Eq '^bge-m3(:|[[:space:]])' && echo bge-m3-ready\nelse\n  echo ollama-not-running\n  exit 4\nfi\n",
    },
    "options_kb_preflight": {
        "purpose": "Inspect whether Options Learning KB can safely consume the already-running private services.",
        "wsl_script": "set -euo pipefail\necho ---application-checkout---\nif [ -d " + KB_ROOT + " ]; then\n  git -C " + KB_ROOT + " rev-parse --short HEAD 2>/dev/null || true\n  test -f " + KB_ROOT + "/compose.yaml && echo compose-present\n  test -f " + KB_ROOT + "/db/migrations/001_options_learning_kb.sql && echo migration-present\nelse\n  echo checkout-absent\nfi\necho ---shared-network---\ndocker network inspect cs-ai-lab_internal --format '{{.Name}} driver={{.Driver}} containers={{len .Containers}}'\necho ---postgres---\ncd " + LAB_ROOT + "\nset -a\nsource .env\nset +a\ndocker compose exec -T postgres pg_isready -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" </dev/null\necho ---ollama-bge---\ndocker compose --profile ollama ps --status running --services | grep -qx ollama\ndocker compose exec -T ollama ollama list | grep -Eq '^bge-m3(:|[[:space:]])'\necho bge-m3-ready\n",
    },
    "options_kb_runtime_status": {
        "purpose": "Inspect deployed Options Learning KB containers only; no logs, data, secrets, or mutations.",
        "wsl_script": "set -euo pipefail\nif [ ! -d " + KB_ROOT + " ]; then echo checkout-absent; exit 4; fi\ncd " + KB_ROOT + "\ndocker compose ps -a\n",
    },
}


def validate_contract() -> None:
    with CATALOG_PATH.open(encoding="utf-8") as file:
        catalog_ids = {entry["id"] for entry in json.load(file)["operations"]}
    if catalog_ids != set(OPERATIONS):
        raise RuntimeError("Adapter and command catalog operation IDs differ.")


def configured_target() -> str:
    target = os.environ.get(SSH_TARGET_ENV, "").strip()
    for config_path in (LOCAL_CONFIG_PATH, SHARED_LAB_CONFIG_PATH):
        if target or not config_path.is_file():
            continue
        for line in config_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{SSH_TARGET_ENV}="):
                target = line.partition("=")[2].strip()
                break
    if not target:
        raise RuntimeError(f"Set {SSH_TARGET_ENV} or add it to ignored {LOCAL_CONFIG_PATH.name}.")
    return target


def ssh_command(target: str, powershell_command: str) -> list[str]:
    encoded_command = base64.b64encode(powershell_command.encode("utf-16-le")).decode("ascii")
    encoded_target = base64.b64encode(target.encode()).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", (
        "$target=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded_target + "')); "
        "$remoteCommand='powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded_command + "'; "
        "$sshArguments=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',$target,$remoteCommand); & ssh.exe @sshArguments"
    )]


def wsl_bash_script_command(script: str) -> str:
    encoded_script = base64.b64encode(script.encode()).decode("ascii")
    return "$ErrorActionPreference='Stop'; '" + encoded_script + "' | wsl.exe -d Ubuntu -- bash -c 'base64 -d | bash'"


def run_command(command: list[str]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip(), "ok": completed.returncode == 0,
    }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def append_execution_log(command_name: str, operation_id: str | None, payload: dict[str, Any]) -> None:
    """Persist metadata/hashes locally without recording T480 output or credentials."""
    result = payload.get("result") or payload.get("remote_check") or {}
    entry = {"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool_id": TOOL_ID,
             "command": command_name, "operation": operation_id, "started_at": result.get("started_at"),
             "finished_at": result.get("finished_at"), "duration_ms": result.get("duration_ms"),
             "exit_code": result.get("exit_code"), "ok": payload.get("ok", result.get("ok")),
             "stdout_bytes": len(result.get("stdout", "")), "stderr_bytes": len(result.get("stderr", "")),
             "stdout_sha256": _digest(result.get("stdout", "")), "stderr_sha256": _digest(result.get("stderr", ""))}
    with EXECUTION_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, separators=(",", ":")) + "\n")


def preflight() -> dict[str, Any]:
    try:
        target = configured_target()
    except RuntimeError as error:
        return {"tool_id": TOOL_ID, "local_checks": {"ssh_available": shutil.which("ssh") is not None,
                "ssh_target_configured": False}, "ok": False, "error": str(error)}
    checks = {"powershell_available": shutil.which("powershell.exe") is not None,
              "windows_ssh_available": shutil.which("ssh.exe") is not None, "ssh_target_configured": bool(target)}
    payload: dict[str, Any] = {"tool_id": TOOL_ID, "local_checks": checks}
    if not all(checks.values()):
        payload["ok"] = False
        return payload
    payload["remote_check"] = run_command(ssh_command(target, "$ErrorActionPreference='Stop'; wsl.exe -d Ubuntu -- bash -lc 'whoami && uname -m'"))
    payload["ok"] = payload["remote_check"]["ok"]
    return payload


def execute(operation_id: str) -> dict[str, Any]:
    details = OPERATIONS.get(operation_id)
    if details is None:
        raise RuntimeError(f"Unknown operation: {operation_id}")
    command = details.get("command") or wsl_bash_script_command(details["wsl_script"])
    result = run_command(ssh_command(configured_target(), command))
    return {"tool_id": TOOL_ID, "operation": operation_id, "approval_required": False, "result": result, "ok": result["ok"]}


def requirements() -> dict[str, Any]:
    return {"tool_id": TOOL_ID, "description": "Run fixed, read-only T480 Windows/WSL/Docker health operations over SSH.",
            "requirements": [f"Set {SSH_TARGET_ENV} or use ignored {LOCAL_CONFIG_PATH.name}.",
                             "Configure SSH key authentication and verify the T480 host key.",
                             "The T480 Windows SSH account must access Ubuntu through wsl.exe."],
            "commands": ["describe-requirements", "preflight", "execute", "verify"],
            "operations": [{"id": name, "purpose": details["purpose"], "approval_required": False} for name, details in OPERATIONS.items()]}


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Governed read-only T480 adapter for Options Learning KB.")
    parser.add_argument("command", choices=["describe-requirements", "preflight", "execute", "verify"])
    parser.add_argument("--operation", choices=sorted(OPERATIONS))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    validate_contract()
    if args.command == "describe-requirements":
        payload = requirements()
    elif args.command == "preflight":
        payload = preflight()
    else:
        if not args.operation:
            raise SystemExit("--operation is required for execute and verify")
        payload = execute(args.operation)
        if args.command == "verify":
            payload["verified_operation"] = args.operation
    append_execution_log(args.command, args.operation, payload)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(json.dumps({"tool_id": TOOL_ID, "ok": False, "error": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(2)
