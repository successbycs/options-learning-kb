from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Iterable, Sequence
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from .db import Database
from .embeddings import EmbeddingProvider
from .transcript import ChunkDraft, Transcript, build_chunks, parse_reviewed_transcript


@dataclass(frozen=True)
class IngestResult:
    run_id: str
    status: str
    source_id: str
    document_id: str | None
    document_count: int
    chunk_count: int
    message: str


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    source_id: str
    lesson_title: str
    timestamp: str
    timestamp_seconds: int
    passage: str
    similarity: float

    @property
    def citation(self) -> str:
        return f"{self.lesson_title} [{self.timestamp}]"


class KnowledgeBaseService:
    def __init__(self, database: Database, embeddings: EmbeddingProvider, embedding_model: str):
        self.database = database
        self.embeddings = embeddings
        self.embedding_model = embedding_model

    def register_source(self, markdown: str, owner: str, permission_basis: str, citation_policy: str | None = None) -> dict:
        if not owner.strip() or not permission_basis.strip():
            raise ValueError("Owner and permission basis are required.")
        transcript = parse_reviewed_transcript(markdown)
        now = datetime.now(timezone.utc)
        source_id = str(uuid4())
        policy = citation_policy or "Private learning/research only; do not redistribute."
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT id FROM sources WHERE source_sha256 = %s", (transcript.source_sha256,))
                existing = cursor.fetchone()
                if existing:
                    source_id = str(existing["id"])
                    cursor.execute(
                        """UPDATE sources SET transcript_sha256=%s, lesson_title=%s, owner=%s,
                           permission_basis=%s, review_status=%s, citation_policy=%s,
                           transcript_markdown=%s, manifest=%s::jsonb, updated_at=now(),
                           approved_at=CASE WHEN %s='APPROVED' THEN COALESCE(approved_at, now()) ELSE approved_at END,
                           disabled_at=CASE WHEN %s='DISABLED' THEN now() ELSE NULL END
                           WHERE id=%s RETURNING *""",
                        (transcript.transcript_sha256, transcript.lesson_title, owner, permission_basis,
                         transcript.review_status, policy, transcript.markdown, _json_manifest(transcript),
                         transcript.review_status, transcript.review_status, source_id),
                    )
                else:
                    cursor.execute(
                        """INSERT INTO sources (
                           id, source_filename, source_sha256, transcript_sha256, lesson_title, owner,
                           permission_basis, review_status, citation_policy, transcript_markdown, manifest, approved_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING *""",
                        (source_id, transcript.source_filename, transcript.source_sha256, transcript.transcript_sha256,
                         transcript.lesson_title, owner, permission_basis, transcript.review_status, policy,
                         transcript.markdown, _json_manifest(transcript), now if transcript.review_status == "APPROVED" else None),
                    )
                row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def update_source_status(self, source_id: str, status: str) -> dict:
        status = status.upper()
        if status not in {"DRAFT", "APPROVED", "DISABLED"}:
            raise ValueError("Status must be DRAFT, APPROVED, or DISABLED.")
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """UPDATE sources SET review_status=%s, updated_at=now(),
                       approved_at=CASE WHEN %s='APPROVED' THEN COALESCE(approved_at, now()) ELSE approved_at END,
                       disabled_at=CASE WHEN %s='DISABLED' THEN now() ELSE NULL END
                       WHERE id=%s RETURNING *""", (status, status, status, source_id),
                )
                row = cursor.fetchone()
                if not row:
                    raise KeyError("Source not found.")
            connection.commit()
        return dict(row)

    def list_sources(self) -> list[dict]:
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """SELECT s.id, s.source_filename, s.source_sha256, s.transcript_sha256, s.lesson_title,
                              s.owner, s.permission_basis, s.review_status, s.created_at, s.approved_at,
                              COUNT(DISTINCT d.id) AS document_count, COUNT(c.id) AS chunk_count
                       FROM sources s LEFT JOIN documents d ON d.source_id=s.id
                       LEFT JOIN chunks c ON c.document_id=d.id
                       GROUP BY s.id ORDER BY s.created_at DESC"""
                )
                return [dict(row) for row in cursor.fetchall()]

    def ingest_source(self, source_id: str) -> IngestResult:
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM sources WHERE id=%s", (source_id,))
                source = cursor.fetchone()
        if not source:
            raise KeyError("Source not found.")
        transcript = parse_reviewed_transcript(source["transcript_markdown"])
        run_key = f"{source_id}:{transcript.transcript_sha256}:{self.embedding_model}"
        if source["review_status"] != "APPROVED":
            return self._save_blocked_run(source, run_key, "Only APPROVED sources may enter retrieval.")

        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM ingest_runs WHERE idempotency_key=%s", (run_key,))
                prior = cursor.fetchone()
                if prior and prior["status"] in {"SUCCEEDED", "SKIPPED"}:
                    return IngestResult(str(prior["id"]), prior["status"], source_id,
                                        str(prior["document_id"]) if prior["document_id"] else None,
                                        prior["document_count"], prior["chunk_count"], "Idempotent re-run: existing ingest retained.")
                cursor.execute("SELECT id FROM documents WHERE source_id=%s AND document_sha256=%s", (source_id, transcript.transcript_sha256))
                document = cursor.fetchone()
                if document:
                    chunk_count = self._count_chunks(connection, str(document["id"]))
                    result = self._finish_run(connection, prior, run_key, source_id, str(document["id"]), "SKIPPED", 1, chunk_count, None)
                    connection.commit()
                    return result
                run_id = str(prior["id"]) if prior else str(uuid4())
                if prior:
                    cursor.execute("UPDATE ingest_runs SET status='STARTED', error_summary=NULL, started_at=now(), finished_at=NULL WHERE id=%s", (run_id,))
                else:
                    cursor.execute(
                        """INSERT INTO ingest_runs (id,idempotency_key,source_id,input_sha256,embedding_model,status)
                           VALUES (%s,%s,%s,%s,%s,'STARTED')""",
                        (run_id, run_key, source_id, transcript.transcript_sha256, self.embedding_model),
                    )
            connection.commit()

        chunks = build_chunks(transcript)
        try:
            vectors = self.embeddings.embed([chunk.passage for chunk in chunks])
            if len(vectors) != len(chunks):
                raise RuntimeError("Embedding provider returned a different number of vectors than chunks.")
            document_id = str(uuid4())
            with self.database.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE documents SET is_current=false WHERE source_id=%s", (source_id,))
                    cursor.execute(
                        """INSERT INTO documents (id,source_id,document_sha256,filename,lesson_title,content_markdown)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (document_id, source_id, transcript.transcript_sha256, transcript.source_filename,
                         transcript.lesson_title, transcript.markdown),
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True):
                        self._insert_chunk(cursor, source_id, document_id, chunk, vector, self.embedding_model)
                    result = self._finish_run(connection, {"id": run_id}, run_key, source_id, document_id,
                                              "SUCCEEDED", 1, len(chunks), None)
                connection.commit()
            return result
        except Exception as error:
            with self.database.connection() as connection:
                self._finish_run(connection, {"id": run_id}, run_key, source_id, None, "FAILED", 0, 0, str(error)[:1000])
                connection.commit()
            raise

    def search(self, question: str, source_ids: Sequence[str] | None = None, limit: int | None = None) -> list[SearchResult]:
        question = question.strip()
        if not question:
            raise ValueError("A search question is required.")
        vector = self.embeddings.embed([question])[0]
        safe_limit = max(1, min(limit or 8, 50))
        scope = [UUID(value) for value in source_ids] if source_ids else None
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM match_approved_chunks(%s, %s, %s)", (vector, scope, safe_limit))
                rows = cursor.fetchall()
        return [SearchResult(
            chunk_id=str(row["chunk_id"]), source_id=str(row["source_id"]), lesson_title=row["lesson_title"],
            timestamp=row["timestamp_label"], timestamp_seconds=row["timestamp_seconds"],
            passage=row["passage"], similarity=float(row["similarity"]),
        ) for row in rows]

    def create_qa_question(self, question: str, expected_source_id: str | None, expected_timestamp_seconds: int | None, expected_timestamp_label: str | None) -> dict:
        qa_id = str(uuid4())
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """INSERT INTO retrieval_qa_questions (id,question,expected_source_id,expected_timestamp_seconds,expected_timestamp_label)
                       VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                    (qa_id, question, expected_source_id, expected_timestamp_seconds, expected_timestamp_label),
                )
                row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def run_qa(self, qa_id: str, tolerance_seconds: int = 90) -> dict:
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM retrieval_qa_questions WHERE id=%s", (qa_id,))
                qa = cursor.fetchone()
        if not qa:
            raise KeyError("QA question not found.")
        results = self.search(qa["question"], [str(qa["expected_source_id"])] if qa["expected_source_id"] else None, 1)
        top = results[0] if results else None
        source_matches = bool(top and (not qa["expected_source_id"] or top.source_id == str(qa["expected_source_id"])))
        timestamp_matches = bool(top and (qa["expected_timestamp_seconds"] is None or abs(top.timestamp_seconds - qa["expected_timestamp_seconds"]) <= tolerance_seconds))
        outcome = "PASS" if source_matches and timestamp_matches else "FAIL"
        run = {"id": str(uuid4()), "result": outcome, "top_chunk_id": top.chunk_id if top else None,
               "expected_source_id": qa["expected_source_id"], "expected_timestamp_seconds": qa["expected_timestamp_seconds"],
               "observed_source_id": top.source_id if top else None, "observed_timestamp_seconds": top.timestamp_seconds if top else None,
               "notes": f"Top result: {top.citation}" if top else "No approved result returned."}
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO retrieval_qa_runs (id,qa_question_id,result,top_chunk_id,expected_source_id,
                       expected_timestamp_seconds,observed_source_id,observed_timestamp_seconds,notes)
                       VALUES (%(id)s,%s,%(result)s,%(top_chunk_id)s,%(expected_source_id)s,%(expected_timestamp_seconds)s,
                               %(observed_source_id)s,%(observed_timestamp_seconds)s,%(notes)s)""", (run, qa_id),
                )
            connection.commit()
        return {**run, "question": qa["question"]}

    def create_gap(self, question: str, source_ids: Sequence[str] | None, rationale: str) -> dict:
        gap_id = str(uuid4())
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "INSERT INTO knowledge_gaps (id,question,source_scope,rationale) VALUES (%s,%s,%s,%s) RETURNING *",
                    (gap_id, question, [UUID(value) for value in source_ids] if source_ids else None, rationale),
                )
                row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def list_qa(self) -> list[dict]:
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("""SELECT q.*, r.result AS last_result, r.notes AS last_notes, r.created_at AS last_run_at
                                  FROM retrieval_qa_questions q
                                  LEFT JOIN LATERAL (SELECT * FROM retrieval_qa_runs WHERE qa_question_id=q.id ORDER BY created_at DESC LIMIT 1) r ON true
                                  ORDER BY q.created_at DESC""")
                return [dict(row) for row in cursor.fetchall()]

    def list_gaps(self) -> list[dict]:
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM knowledge_gaps ORDER BY created_at DESC")
                return [dict(row) for row in cursor.fetchall()]

    def list_ingest_runs(self, source_id: str | None = None) -> list[dict]:
        with self.database.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                if source_id:
                    cursor.execute("SELECT * FROM ingest_runs WHERE source_id=%s ORDER BY started_at DESC", (source_id,))
                else:
                    cursor.execute("SELECT * FROM ingest_runs ORDER BY started_at DESC")
                return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _count_chunks(connection, document_id: str) -> int:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM chunks WHERE document_id=%s", (document_id,))
            return int(cursor.fetchone()[0])

    def _save_blocked_run(self, source: dict, run_key: str, message: str) -> IngestResult:
        with self.database.connection() as connection:
            result = self._finish_run(connection, None, run_key, str(source["id"]), None, "BLOCKED", 0, 0, message)
            connection.commit()
        return result

    @staticmethod
    def _insert_chunk(cursor, source_id: str, document_id: str, chunk: ChunkDraft, vector: list[float], embedding_model: str) -> None:
        cursor.execute(
            """INSERT INTO chunks (id,source_id,document_id,ordinal,timestamp_start_seconds,timestamp_start_label,
               timestamp_end_seconds,timestamp_end_label,chunk_sha256,passage,embedding_model,embedding)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (str(uuid4()), source_id, document_id, chunk.ordinal, chunk.timestamp_start_seconds,
             chunk.timestamp_start_label, chunk.timestamp_end_seconds, chunk.timestamp_end_label,
             chunk.chunk_sha256, chunk.passage, embedding_model, vector),
        )

    @staticmethod
    def _finish_run(connection, prior: dict | None, key: str, source_id: str, document_id: str | None,
                    status: str, document_count: int, chunk_count: int, error: str | None) -> IngestResult:
        run_id = str(prior["id"]) if prior else str(uuid4())
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """INSERT INTO ingest_runs (id,idempotency_key,source_id,document_id,input_sha256,embedding_model,status,
                   document_count,chunk_count,error_summary,finished_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (idempotency_key) DO UPDATE SET document_id=EXCLUDED.document_id,status=EXCLUDED.status,
                   document_count=EXCLUDED.document_count,chunk_count=EXCLUDED.chunk_count,error_summary=EXCLUDED.error_summary,
                   finished_at=now() RETURNING *""",
                (run_id, key, source_id, document_id, key.split(":")[1], key.split(":")[2], status,
                 document_count, chunk_count, error),
            )
            row = cursor.fetchone()
        message = error or ("Ingest completed." if status == "SUCCEEDED" else "No duplicate document/chunks were created.")
        return IngestResult(str(row["id"]), status, source_id, str(row["document_id"]) if row["document_id"] else None,
                            row["document_count"], row["chunk_count"], message)


def _json_manifest(transcript: Transcript) -> str:
    import json
    return json.dumps(dict(transcript.metadata), sort_keys=True)
