"""Evidence-first milestone lifecycle for the private Options Learning KB."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

STATE_RELATIVE_PATH = Path("data/milestone-state.json")
EVIDENCE_RELATIVE_PATH = Path("data/milestone-evidence")
VALID_STATES = {"not_started", "in_progress", "blocked", "needs_fix", "complete"}


class GovernanceError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp() -> str:
    return utc_now().isoformat()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GovernanceError(f"Milestone registry is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise GovernanceError(f"Milestone registry is malformed: {error}") from error
    validate_registry(payload)
    return payload


def validate_registry(payload: dict[str, Any]) -> None:
    milestones = payload.get("milestones") if isinstance(payload, dict) else None
    if not isinstance(milestones, list) or not milestones:
        raise GovernanceError("Milestone registry must contain a non-empty milestones list.")
    seen: set[str] = set()
    for milestone in milestones:
        if not isinstance(milestone, dict):
            raise GovernanceError("Each milestone must be an object.")
        milestone_id = str(milestone.get("id") or "").strip()
        if not milestone_id or milestone_id in seen:
            raise GovernanceError("Milestones require unique IDs.")
        seen.add(milestone_id)
        if milestone.get("status", "not_started") == "complete":
            raise GovernanceError(f"{milestone_id} cannot be completed in static registry data.")
        required = ("title", "intent", "dependencies", "entry_conditions", "proof_contract")
        missing = [key for key in required if key not in milestone]
        missing.extend(
            key
            for key in ("title", "intent", "entry_conditions", "proof_contract")
            if key in milestone and not milestone[key]
        )
        if missing:
            raise GovernanceError(f"{milestone_id} is missing: {', '.join(missing)}")
        if not isinstance(milestone["dependencies"], list) or not isinstance(milestone["entry_conditions"], list):
            raise GovernanceError(f"{milestone_id} dependencies and entry_conditions must be lists.")
        contract = milestone["proof_contract"]
        if not isinstance(contract, dict):
            raise GovernanceError(f"{milestone_id} proof_contract must be an object.")
        required_contract = ("real_surface", "capture_command", "required_markers", "freshness_hours", "verifier")
        missing_contract = [key for key in required_contract if not contract.get(key)]
        if missing_contract:
            raise GovernanceError(f"{milestone_id} proof contract is missing: {', '.join(missing_contract)}")
        if not isinstance(contract["capture_command"], list) or not all(
            isinstance(item, str) and item for item in contract["capture_command"]
        ):
            raise GovernanceError(f"{milestone_id} capture_command must be a non-empty fixed argv list.")
        if not isinstance(contract["required_markers"], list):
            raise GovernanceError(f"{milestone_id} required_markers must be a list.")
        try:
            if int(contract["freshness_hours"]) <= 0:
                raise ValueError
        except (TypeError, ValueError) as error:
            raise GovernanceError(f"{milestone_id} freshness_hours must be positive.") from error
    unknown = {
        dependency for milestone in milestones for dependency in milestone["dependencies"] if dependency not in seen
    }
    if unknown:
        raise GovernanceError(f"Unknown milestone dependencies: {', '.join(sorted(unknown))}")


def milestones_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in registry["milestones"]}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "milestones": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GovernanceError("Milestone state is malformed JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("milestones"), dict):
        raise GovernanceError("Milestone state must contain a milestones object.")
    return payload


def _local_state(state: dict[str, Any], milestone_id: str) -> dict[str, Any]:
    return state["milestones"].setdefault(milestone_id, {"status": "not_started", "evidence": [], "history": []})


def _record(local: dict[str, Any], event: str, details: dict[str, Any] | None = None) -> None:
    local["history"].append({"at": timestamp(), "event": event, "details": details or {}})


def _dependencies_missing(registry: dict[str, Any], state: dict[str, Any], milestone: dict[str, Any]) -> list[str]:
    definitions = milestones_by_id(registry)
    return [
        dependency
        for dependency in milestone["dependencies"]
        if definitions[dependency] and state["milestones"].get(dependency, {}).get("status") != "complete"
    ]


def start_milestone(registry: dict[str, Any], state: dict[str, Any], milestone_id: str) -> None:
    milestone = milestones_by_id(registry).get(milestone_id)
    if not milestone:
        raise GovernanceError(f"Unknown milestone: {milestone_id}")
    missing = _dependencies_missing(registry, state, milestone)
    if missing:
        raise GovernanceError(f"{milestone_id} is blocked until complete: {', '.join(missing)}")
    local = _local_state(state, milestone_id)
    if local["status"] == "complete":
        raise GovernanceError(f"{milestone_id} is complete and cannot be restarted.")
    local["status"] = "in_progress"
    _record(local, "started")


def set_noncompletion(
    registry: dict[str, Any], state: dict[str, Any], milestone_id: str, status: str, reason: str
) -> None:
    if status not in {"blocked", "needs_fix"} or not reason.strip():
        raise GovernanceError("A concrete blocked or needs_fix reason is required.")
    if milestone_id not in milestones_by_id(registry):
        raise GovernanceError(f"Unknown milestone: {milestone_id}")
    local = _local_state(state, milestone_id)
    if local["status"] == "complete":
        raise GovernanceError("Completed milestones cannot be changed by non-completion actions.")
    local["status"] = status
    _record(local, status, {"reason": reason.strip()})


def capture_evidence(registry: dict[str, Any], state: dict[str, Any], milestone_id: str, root: Path) -> dict[str, Any]:
    milestone = milestones_by_id(registry).get(milestone_id)
    if not milestone:
        raise GovernanceError(f"Unknown milestone: {milestone_id}")
    local = _local_state(state, milestone_id)
    if local["status"] != "in_progress":
        raise GovernanceError(f"{milestone_id} must be in_progress before evidence capture.")
    command = milestone["proof_contract"]["capture_command"]
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise GovernanceError(f"{milestone_id} evidence capture timed out.") from error
    if completed.returncode != 0:
        raise GovernanceError(
            f"{milestone_id} capture failed: {completed.stderr.strip() or 'exit ' + str(completed.returncode)}"
        )
    capture_id = f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    evidence_path = root / EVIDENCE_RELATIVE_PATH / milestone_id / f"{capture_id}.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(completed.stdout, encoding="utf-8")
    evidence = {
        "capture_id": capture_id,
        "path": str(evidence_path.relative_to(root)),
        "sha256": file_sha256(evidence_path),
        "captured_at": timestamp(),
        "captured_by": "declared_registry_command",
        "command": command,
    }
    local["evidence"].append(evidence)
    _record(local, "evidence_captured", {"capture_id": capture_id, "path": evidence["path"]})
    return evidence


def _load_verified_evidence(
    registry: dict[str, Any], state: dict[str, Any], milestone_id: str, root: Path
) -> tuple[dict[str, Any], dict[str, Any], str]:
    milestone = milestones_by_id(registry).get(milestone_id)
    if not milestone:
        raise GovernanceError(f"Unknown milestone: {milestone_id}")
    local = _local_state(state, milestone_id)
    if local["status"] not in {"in_progress", "complete"}:
        raise GovernanceError(f"{milestone_id} must be in_progress or complete before verification.")
    evidence_entries = local.get("evidence") or []
    if not evidence_entries:
        raise GovernanceError(f"{milestone_id} has no captured primary evidence.")
    evidence = evidence_entries[-1]
    if evidence.get("captured_by") != "declared_registry_command":
        raise GovernanceError("Evidence did not come from the declared capture command.")
    path = root / str(evidence.get("path") or "")
    evidence_root = (root / EVIDENCE_RELATIVE_PATH).resolve()
    if not path.is_file() or not path.resolve().is_relative_to(evidence_root):
        raise GovernanceError("Primary evidence must remain under ignored data/milestone-evidence.")
    if file_sha256(path) != evidence.get("sha256"):
        raise GovernanceError("Primary evidence changed after capture.")
    try:
        age = utc_now() - datetime.fromisoformat(str(evidence["captured_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as error:
        raise GovernanceError("Primary evidence capture time is invalid.") from error
    if age > timedelta(hours=int(milestone["proof_contract"]["freshness_hours"])):
        raise GovernanceError(f"Primary evidence is stale ({age}).")
    content = path.read_text(encoding="utf-8")
    missing = [marker for marker in milestone["proof_contract"]["required_markers"] if marker not in content]
    if missing:
        raise GovernanceError(f"Primary evidence lacks required markers: {', '.join(missing)}")
    return milestone, evidence, content


def verify_milestone(
    registry: dict[str, Any],
    state: dict[str, Any],
    milestone_id: str,
    root: Path,
    independent_verifiers: dict[str, Callable[[Path], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    milestone, evidence, _ = _load_verified_evidence(registry, state, milestone_id, root)
    verifier_name = str(milestone["proof_contract"]["verifier"])
    if verifier_name == "registry_self_check":
        validate_registry(registry)
        readback = {"verifier": verifier_name, "result": "registry_valid"}
    elif independent_verifiers and verifier_name in independent_verifiers:
        readback = independent_verifiers[verifier_name](root)
    else:
        raise GovernanceError(f"Verifier is not implemented: {verifier_name}")
    result = {
        "milestone_id": milestone_id,
        "verified_at": timestamp(),
        "primary_evidence": evidence["path"],
        "verifier": verifier_name,
        "independent_readback": readback,
    }
    local = _local_state(state, milestone_id)
    local["last_verification"] = result
    _record(local, "verified", result)
    return result


def complete_milestone(
    registry: dict[str, Any],
    state: dict[str, Any],
    milestone_id: str,
    root: Path,
    independent_verifiers: dict[str, Callable[[Path], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    result = verify_milestone(registry, state, milestone_id, root, independent_verifiers)
    local = _local_state(state, milestone_id)
    local["status"] = "complete"
    local["completion"] = result
    _record(local, "completed", result)
    return result


def status_report(registry: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    for milestone_id, milestone in milestones_by_id(registry).items():
        local = state["milestones"].get(milestone_id, {})
        result.append(
            {
                "id": milestone_id,
                "title": milestone["title"],
                "status": local.get("status", "not_started"),
                "blocked_by": _dependencies_missing(registry, state, milestone),
                "evidence_count": len(local.get("evidence", [])),
            }
        )
    return {"milestones": result}
