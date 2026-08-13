import json

from scripts import t480_adapter


def test_catalog_and_adapter_are_locked_to_the_same_operations():
    t480_adapter.validate_contract()
    catalog = json.loads(t480_adapter.CATALOG_PATH.read_text())
    assert {item["id"] for item in catalog["operations"]} == set(t480_adapter.OPERATIONS)


def test_adapter_has_no_mutating_operations_or_free_form_command_argument():
    requirements = t480_adapter.requirements()
    assert all(not item["approval_required"] for item in requirements["operations"])
    assert "--command" not in t480_adapter.parser().format_help()


def test_local_bge_check_is_part_of_the_fixed_catalog():
    script = t480_adapter.OPERATIONS["ollama_bge_status"]["wsl_script"]
    assert "bge-m3" in script
    assert "ollama list" in script
