from pathlib import Path


def test_migration_has_required_private_retrieval_controls():
    sql = Path("db/migrations/001_options_learning_kb.sql").read_text()

    for required in (
        "CREATE TABLE IF NOT EXISTS sources",
        "CREATE TABLE IF NOT EXISTS documents",
        "CREATE TABLE IF NOT EXISTS chunks",
        "CREATE TABLE IF NOT EXISTS ingest_runs",
        "CREATE TABLE IF NOT EXISTS retrieval_qa_questions",
        "CREATE TABLE IF NOT EXISTS knowledge_gaps",
        "VECTOR(1024)",
        "transcript_review_status",
        "match_approved_chunks",
        "source_sha256 CHAR(64)",
        "document_sha256 CHAR(64)",
        "chunk_sha256 CHAR(64)",
        "s.review_status = 'APPROVED'",
        "d.is_current = true",
    ):
        assert required in sql


def test_delivery_registry_requires_real_surface_proof_contracts():
    import json

    from options_learning_kb.milestone_governance import validate_registry

    registry = json.loads(Path("milestones/registry.json").read_text())
    validate_registry(registry)
    assert registry["milestones"][0]["id"] == "KB-M0"
    assert registry["milestones"][1]["proof_contract"]["verifier"] == "t480_retrieval_dependencies_readback"
