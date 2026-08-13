import json
import sys

import pytest

from options_learning_kb import milestone_governance as governance


def registry(command=None):
    return {
        "milestones": [
            {
                "id": "KB-M0",
                "title": "Controller",
                "status": "not_started",
                "intent": "Prove evidence flow.",
                "dependencies": [],
                "entry_conditions": ["Registry exists."],
                "proof_contract": {
                    "real_surface": "A real local subprocess.",
                    "capture_command": command or [sys.executable, "-c", "print('KB_MILESTONE_CONTROLLER_VALID')"],
                    "required_markers": ["KB_MILESTONE_CONTROLLER_VALID"],
                    "freshness_hours": 1,
                    "verifier": "registry_self_check",
                },
            }
        ]
    }


def test_completion_requires_declared_hashed_evidence(tmp_path):
    definitions = registry()
    state = {"version": 1, "milestones": {}}
    governance.start_milestone(definitions, state, "KB-M0")
    with pytest.raises(governance.GovernanceError, match="no captured"):
        governance.complete_milestone(definitions, state, "KB-M0", tmp_path)

    evidence = governance.capture_evidence(definitions, state, "KB-M0", tmp_path)
    result = governance.complete_milestone(definitions, state, "KB-M0", tmp_path)

    assert result["primary_evidence"] == evidence["path"]
    assert state["milestones"]["KB-M0"]["status"] == "complete"


def test_tampered_evidence_cannot_be_verified(tmp_path):
    definitions = registry()
    state = {"version": 1, "milestones": {}}
    governance.start_milestone(definitions, state, "KB-M0")
    evidence = governance.capture_evidence(definitions, state, "KB-M0", tmp_path)
    (tmp_path / evidence["path"]).write_text("KB_MILESTONE_CONTROLLER_VALID\ntampered\n")

    with pytest.raises(governance.GovernanceError, match="changed after capture"):
        governance.verify_milestone(definitions, state, "KB-M0", tmp_path)


def test_dependencies_and_static_completion_are_rejected(tmp_path):
    definitions = registry()
    dependent = json.loads(json.dumps(definitions))
    dependent["milestones"].append(
        {
            "id": "KB-M1",
            "title": "Dependent",
            "status": "not_started",
            "intent": "Wait for M0.",
            "dependencies": ["KB-M0"],
            "entry_conditions": ["M0 complete."],
            "proof_contract": {
                "real_surface": "A real local subprocess.",
                "capture_command": [sys.executable, "-c", "print('DEPENDENT')"],
                "required_markers": ["DEPENDENT"],
                "freshness_hours": 1,
                "verifier": "registry_self_check",
            },
        }
    )
    state = {"version": 1, "milestones": {}}
    with pytest.raises(governance.GovernanceError, match="blocked until complete"):
        governance.start_milestone(dependent, state, "KB-M1")

    definitions["milestones"][0]["status"] = "complete"
    with pytest.raises(governance.GovernanceError, match="cannot be completed"):
        governance.validate_registry(definitions)


def test_independent_verifier_is_required_for_non_self_check(tmp_path):
    definitions = registry()
    definitions["milestones"][0]["proof_contract"]["verifier"] = "remote_readback"
    state = {"version": 1, "milestones": {}}
    governance.start_milestone(definitions, state, "KB-M0")
    governance.capture_evidence(definitions, state, "KB-M0", tmp_path)
    with pytest.raises(governance.GovernanceError, match="not implemented"):
        governance.verify_milestone(definitions, state, "KB-M0", tmp_path)

    result = governance.verify_milestone(
        definitions, state, "KB-M0", tmp_path, {"remote_readback": lambda root: {"real_surface_checked": True}}
    )
    assert result["independent_readback"]["real_surface_checked"] is True
