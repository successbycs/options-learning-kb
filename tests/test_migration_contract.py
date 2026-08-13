from pathlib import Path


def test_migration_has_required_private_retrieval_controls():
    sql = Path("db/migrations/001_options_learning_kb.sql").read_text()

    for required in ("CREATE TABLE IF NOT EXISTS sources", "CREATE TABLE IF NOT EXISTS documents",
                     "CREATE TABLE IF NOT EXISTS chunks", "CREATE TABLE IF NOT EXISTS ingest_runs",
                     "CREATE TABLE IF NOT EXISTS retrieval_qa_questions", "CREATE TABLE IF NOT EXISTS knowledge_gaps",
                     "VECTOR(1024)", "match_approved_chunks", "s.review_status = 'APPROVED'", "d.is_current = true"):
        assert required in sql
