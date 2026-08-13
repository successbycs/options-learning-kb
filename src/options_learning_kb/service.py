from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from .db import Database
from .embeddings import EmbeddingProvider
from .transcript import ChunkDraft, Transcript, build_chunks, parse_reviewed_transcript

DEFAULT_CITATION_POLICY = "Private learning/research only; do not redistribute."
SOURCE_STATUSES = frozenset({"DRAFT", "APPROVED", "DISABLED"})


class SourceNotFoundError(LookupError):
    pass


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
    source_sha256: str
    document_sha256: str
    chunk_sha256: str

    @property
    def citation(self) -> str:
        return f"{self.lesson_title} [{self.timestamp}]"


class KnowledgeBaseService:
    def __init__(
        self, database: Database, embeddings: EmbeddingProvider, embedding_model: str, *, default_search_limit: int = 8
    ):
        self.database = database
        self.embeddings = embeddings
        self.embedding_model = embedding_model
        self.default_search_limit = _validated_limit(default_search_limit)

    def register_source(
        self, markdown: str, owner: str, permission_basis: str, citation_policy: str | None = None
    ) -> dict[str, Any]:
        """Register a private transcript as DRAFT; operator approval is separate.

        The transcript's own review status remains recorded and is required for
        later operator approval. A file cannot self-authorise retrieval.
        """
        owner, permission_basis = _required_text(owner, "Owner"), _required_text(permission_basis, "Permission basis")
        transcript = parse_reviewed_transcript(markdown)
        policy = (citation_policy or DEFAULT_CITATION_POLICY).strip()
        if not policy:
            raise ValueError("Citation policy is required.")

        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT id FROM sources WHERE source_sha256 = %s", (transcript.source_sha256,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    """UPDATE sources SET source_filename=%s, transcript_sha256=%s, lesson_title=%s, owner=%s,
                       permission_basis=%s, transcript_review_status=%s, review_status='DRAFT', citation_policy=%s,
                       transcript_markdown=%s, manifest=%s::jsonb, updated_at=now(), approved_at=NULL, disabled_at=NULL
                       WHERE id=%s RETURNING *""",
                    (
                        transcript.source_filename,
                        transcript.transcript_sha256,
                        transcript.lesson_title,
                        owner,
                        permission_basis,
                        transcript.review_status,
                        policy,
                        transcript.markdown,
                        _manifest_json(transcript),
                        existing["id"],
                    ),
                )
            else:
                cursor.execute(
                    """INSERT INTO sources (id,source_filename,source_sha256,transcript_sha256,lesson_title,owner,
                       permission_basis,transcript_review_status,review_status,citation_policy,transcript_markdown,manifest)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s,%s,%s::jsonb) RETURNING *""",
                    (
                        uuid4(),
                        transcript.source_filename,
                        transcript.source_sha256,
                        transcript.transcript_sha256,
                        transcript.lesson_title,
                        owner,
                        permission_basis,
                        transcript.review_status,
                        policy,
                        transcript.markdown,
                        _manifest_json(transcript),
                    ),
                )
            row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def update_source_status(self, source_id: str, status: str) -> dict[str, Any]:
        status = _source_status(status)
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT transcript_review_status FROM sources WHERE id=%s FOR UPDATE", (UUID(source_id),))
            source = cursor.fetchone()
            if not source:
                raise SourceNotFoundError("Source not found.")
            if status == "APPROVED" and source["transcript_review_status"] != "APPROVED":
                raise ValueError("The uploaded transcript must be marked APPROVED before the source can be approved.")
            cursor.execute(
                """UPDATE sources SET review_status=%s, updated_at=now(),
                   approved_at=CASE WHEN %s='APPROVED' THEN now() ELSE NULL END,
                   disabled_at=CASE WHEN %s='DISABLED' THEN now() ELSE NULL END
                   WHERE id=%s RETURNING *""",
                (status, status, status, UUID(source_id)),
            )
            row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def list_sources(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """SELECT s.id, s.source_filename, s.source_sha256, s.transcript_sha256, s.lesson_title,
                          s.owner, s.permission_basis, s.transcript_review_status, s.review_status,
                          s.created_at, s.approved_at, COUNT(DISTINCT d.id) AS document_count,
                          COUNT(c.id) FILTER (WHERE d.is_current) AS chunk_count
                   FROM sources s
                   LEFT JOIN documents d ON d.source_id=s.id
                   LEFT JOIN chunks c ON c.document_id=d.id
                   GROUP BY s.id ORDER BY s.created_at DESC"""
            )
            return [dict(row) for row in cursor.fetchall()]

    def ingest_source(self, source_id: str) -> IngestResult:
        source = self._source(source_id)
        transcript = parse_reviewed_transcript(source["transcript_markdown"])
        run_key = _ingest_idempotency_key(str(source["id"]), transcript.transcript_sha256, self.embedding_model)
        if source["review_status"] != "APPROVED":
            return self._record_terminal_run(
                source,
                run_key,
                transcript.transcript_sha256,
                "BLOCKED",
                None,
                0,
                0,
                "Only operator-approved sources may enter retrieval.",
            )
        if transcript.review_status != "APPROVED":
            return self._record_terminal_run(
                source,
                run_key,
                transcript.transcript_sha256,
                "BLOCKED",
                None,
                0,
                0,
                "The transcript review status is not APPROVED.",
            )

        existing = self._existing_ingest(run_key)
        if existing and existing["status"] in {"SUCCEEDED", "SKIPPED"}:
            return _ingest_result(existing, "Idempotent re-run: existing ingest retained.")

        document = self._document_for_hash(str(source["id"]), transcript.transcript_sha256)
        if document:
            return self._record_terminal_run(
                source,
                run_key,
                transcript.transcript_sha256,
                "SKIPPED",
                str(document["id"]),
                1,
                self._chunk_count(str(document["id"])),
                None,
                existing,
            )

        run_id = self._start_run(source, run_key, transcript.transcript_sha256, existing)
        chunks = build_chunks(transcript)
        try:
            vectors = self.embeddings.embed([chunk.passage for chunk in chunks])
            if len(vectors) != len(chunks):
                raise RuntimeError("Embedding provider returned a different number of vectors than chunks.")
            return self._persist_document_and_chunks(source, transcript, run_key, run_id, chunks, vectors)
        except Exception as error:
            self._record_terminal_run(
                source,
                run_key,
                transcript.transcript_sha256,
                "FAILED",
                None,
                0,
                0,
                f"{type(error).__name__}: {error}"[:1000],
                {"id": run_id},
            )
            raise

    def search(
        self, question: str, source_ids: Sequence[str] | None = None, limit: int | None = None
    ) -> list[SearchResult]:
        question = _required_text(question, "Question")
        query_embedding = self.embeddings.embed([question])[0]
        source_scope = [UUID(source_id) for source_id in source_ids] if source_ids else None
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM match_approved_chunks(%s, %s, %s)",
                (query_embedding, source_scope, _validated_limit(limit or self.default_search_limit)),
            )
            rows = cursor.fetchall()
        return [
            SearchResult(
                chunk_id=str(row["chunk_id"]),
                source_id=str(row["source_id"]),
                lesson_title=row["lesson_title"],
                timestamp=row["timestamp_label"],
                timestamp_seconds=row["timestamp_seconds"],
                passage=row["passage"],
                similarity=float(row["similarity"]),
                source_sha256=row["source_sha256"],
                document_sha256=row["document_sha256"],
                chunk_sha256=row["chunk_sha256"],
            )
            for row in rows
        ]

    def create_qa_question(
        self,
        question: str,
        expected_source_id: str | None,
        expected_timestamp_seconds: int | None,
        expected_timestamp_label: str | None,
    ) -> dict[str, Any]:
        question = _required_text(question, "Question")
        if expected_timestamp_seconds is not None and expected_timestamp_seconds < 0:
            raise ValueError("Expected timestamp must be non-negative.")
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """INSERT INTO retrieval_qa_questions (id,question,expected_source_id,expected_timestamp_seconds,expected_timestamp_label)
                   VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                (
                    uuid4(),
                    question,
                    UUID(expected_source_id) if expected_source_id else None,
                    expected_timestamp_seconds,
                    expected_timestamp_label,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def run_qa(self, qa_id: str, tolerance_seconds: int = 90) -> dict[str, Any]:
        if tolerance_seconds < 0:
            raise ValueError("Timestamp tolerance must be non-negative.")
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM retrieval_qa_questions WHERE id=%s", (UUID(qa_id),))
            qa = cursor.fetchone()
        if not qa:
            raise SourceNotFoundError("QA question not found.")
        results = self.search(qa["question"], [str(qa["expected_source_id"])] if qa["expected_source_id"] else None, 1)
        top = results[0] if results else None
        source_matches = top is not None and (
            qa["expected_source_id"] is None or top.source_id == str(qa["expected_source_id"])
        )
        timestamp_matches = top is not None and (
            qa["expected_timestamp_seconds"] is None
            or abs(top.timestamp_seconds - qa["expected_timestamp_seconds"]) <= tolerance_seconds
        )
        result = "PASS" if source_matches and timestamp_matches else "FAIL"
        run = {
            "id": uuid4(),
            "result": result,
            "top_chunk_id": UUID(top.chunk_id) if top else None,
            "expected_source_id": qa["expected_source_id"],
            "expected_timestamp_seconds": qa["expected_timestamp_seconds"],
            "observed_source_id": UUID(top.source_id) if top else None,
            "observed_timestamp_seconds": top.timestamp_seconds if top else None,
            "notes": f"Top result: {top.citation}" if top else "No approved result returned.",
        }
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO retrieval_qa_runs (id,qa_question_id,result,top_chunk_id,expected_source_id,
                   expected_timestamp_seconds,observed_source_id,observed_timestamp_seconds,notes)
                   VALUES (%(id)s,%s,%(result)s,%(top_chunk_id)s,%(expected_source_id)s,%(expected_timestamp_seconds)s,
                           %(observed_source_id)s,%(observed_timestamp_seconds)s,%(notes)s)""",
                (run, UUID(qa_id)),
            )
            connection.commit()
        return {
            **{key: str(value) if isinstance(value, UUID) else value for key, value in run.items()},
            "question": qa["question"],
        }

    def create_gap(self, question: str, source_ids: Sequence[str] | None, rationale: str) -> dict[str, Any]:
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "INSERT INTO knowledge_gaps (id,question,source_scope,rationale) VALUES (%s,%s,%s,%s) RETURNING *",
                (
                    uuid4(),
                    _required_text(question, "Question"),
                    [UUID(value) for value in source_ids] if source_ids else None,
                    _required_text(rationale, "Gap rationale"),
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        return dict(row)

    def list_qa(self) -> list[dict[str, Any]]:
        return self._list("""SELECT q.*, r.result AS last_result, r.notes AS last_notes, r.created_at AS last_run_at
                             FROM retrieval_qa_questions q
                             LEFT JOIN LATERAL (SELECT * FROM retrieval_qa_runs WHERE qa_question_id=q.id ORDER BY created_at DESC LIMIT 1) r ON true
                             ORDER BY q.created_at DESC""")

    def list_gaps(self) -> list[dict[str, Any]]:
        return self._list("SELECT * FROM knowledge_gaps ORDER BY created_at DESC")

    def list_ingest_runs(self, source_id: str | None = None) -> list[dict[str, Any]]:
        if source_id:
            return self._list(
                "SELECT * FROM ingest_runs WHERE source_id=%s ORDER BY started_at DESC", (UUID(source_id),)
            )
        return self._list("SELECT * FROM ingest_runs ORDER BY started_at DESC")

    def _source(self, source_id: str) -> Mapping[str, Any]:
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM sources WHERE id=%s", (UUID(source_id),))
            source = cursor.fetchone()
        if not source:
            raise SourceNotFoundError("Source not found.")
        return source

    def _existing_ingest(self, idempotency_key: str) -> Mapping[str, Any] | None:
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM ingest_runs WHERE idempotency_key=%s", (idempotency_key,))
            return cursor.fetchone()

    def _document_for_hash(self, source_id: str, document_sha256: str) -> Mapping[str, Any] | None:
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id FROM documents WHERE source_id=%s AND document_sha256=%s", (UUID(source_id), document_sha256)
            )
            return cursor.fetchone()

    def _chunk_count(self, document_id: str) -> int:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM chunks WHERE document_id=%s", (UUID(document_id),))
            return int(cursor.fetchone()[0])

    def _start_run(
        self, source: Mapping[str, Any], key: str, input_sha256: str, existing: Mapping[str, Any] | None
    ) -> UUID:
        run_id = UUID(str(existing["id"])) if existing else uuid4()
        with self.database.connection() as connection, connection.cursor() as cursor:
            if existing:
                cursor.execute(
                    "UPDATE ingest_runs SET status='STARTED', error_summary=NULL, started_at=now(), finished_at=NULL WHERE id=%s",
                    (run_id,),
                )
            else:
                cursor.execute(
                    """INSERT INTO ingest_runs (id,idempotency_key,source_id,input_sha256,embedding_model,status)
                                  VALUES (%s,%s,%s,%s,%s,'STARTED')""",
                    (run_id, key, source["id"], input_sha256, self.embedding_model),
                )
            connection.commit()
        return run_id

    def _persist_document_and_chunks(
        self,
        source: Mapping[str, Any],
        transcript: Transcript,
        run_key: str,
        run_id: UUID,
        chunks: Sequence[ChunkDraft],
        vectors: Sequence[list[float]],
    ) -> IngestResult:
        document_id = uuid4()
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE documents SET is_current=false WHERE source_id=%s", (source["id"],))
            cursor.execute(
                """INSERT INTO documents (id,source_id,document_sha256,filename,lesson_title,content_markdown)
                              VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    document_id,
                    source["id"],
                    transcript.transcript_sha256,
                    transcript.source_filename,
                    transcript.lesson_title,
                    transcript.markdown,
                ),
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._insert_chunk(cursor, source["id"], document_id, chunk, vector, self.embedding_model)
            result = self._finish_run(
                cursor,
                run_id,
                run_key,
                source["id"],
                transcript.transcript_sha256,
                document_id,
                "SUCCEEDED",
                1,
                len(chunks),
                None,
            )
            connection.commit()
        return result

    def _record_terminal_run(
        self,
        source: Mapping[str, Any],
        run_key: str,
        input_sha256: str,
        status: str,
        document_id: str | None,
        document_count: int,
        chunk_count: int,
        error: str | None,
        existing: Mapping[str, Any] | None = None,
    ) -> IngestResult:
        run_id = UUID(str(existing["id"])) if existing else uuid4()
        with self.database.connection() as connection, connection.cursor() as cursor:
            result = self._finish_run(
                cursor,
                run_id,
                run_key,
                source["id"],
                input_sha256,
                UUID(document_id) if document_id else None,
                status,
                document_count,
                chunk_count,
                error,
            )
            connection.commit()
        return result

    def _finish_run(
        self,
        cursor: Any,
        run_id: UUID,
        key: str,
        source_id: UUID,
        input_sha256: str,
        document_id: UUID | None,
        status: str,
        document_count: int,
        chunk_count: int,
        error: str | None,
    ) -> IngestResult:
        cursor.execute(
            """INSERT INTO ingest_runs (id,idempotency_key,source_id,document_id,input_sha256,embedding_model,status,
               document_count,chunk_count,error_summary,finished_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
               ON CONFLICT (idempotency_key) DO UPDATE SET document_id=EXCLUDED.document_id,status=EXCLUDED.status,
               document_count=EXCLUDED.document_count,chunk_count=EXCLUDED.chunk_count,error_summary=EXCLUDED.error_summary,
               finished_at=now() RETURNING *""",
            (
                run_id,
                key,
                source_id,
                document_id,
                input_sha256,
                self.embedding_model,
                status,
                document_count,
                chunk_count,
                error,
            ),
        )
        row = cursor.fetchone()
        message = error or (
            "Ingest completed." if status == "SUCCEEDED" else "No duplicate document/chunks were created."
        )
        return IngestResult(
            str(row[0]),
            status,
            str(source_id),
            str(document_id) if document_id else None,
            document_count,
            chunk_count,
            message,
        )

    def _list(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.database.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(statement, parameters)
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _insert_chunk(
        cursor: Any, source_id: UUID, document_id: UUID, chunk: ChunkDraft, vector: list[float], embedding_model: str
    ) -> None:
        cursor.execute(
            """INSERT INTO chunks (id,source_id,document_id,ordinal,timestamp_start_seconds,timestamp_start_label,
               timestamp_end_seconds,timestamp_end_label,chunk_sha256,passage,embedding_model,embedding)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(),
                source_id,
                document_id,
                chunk.ordinal,
                chunk.timestamp_start_seconds,
                chunk.timestamp_start_label,
                chunk.timestamp_end_seconds,
                chunk.timestamp_end_label,
                chunk.chunk_sha256,
                chunk.passage,
                embedding_model,
                vector,
            ),
        )


def _ingest_idempotency_key(source_id: str, transcript_sha256: str, embedding_model: str) -> str:
    return f"ingest:{UUID(source_id)}:{transcript_sha256}:{embedding_model}"


def _manifest_json(transcript: Transcript) -> str:
    return json.dumps(dict(transcript.metadata), sort_keys=True, separators=(",", ":"))


def _required_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} is required.")
    return normalized


def _source_status(value: str) -> str:
    status = value.strip().upper()
    if status not in SOURCE_STATUSES:
        raise ValueError("Status must be DRAFT, APPROVED, or DISABLED.")
    return status


def _validated_limit(value: int) -> int:
    return max(1, min(int(value), 50))


def _ingest_result(row: Mapping[str, Any], message: str) -> IngestResult:
    return IngestResult(
        str(row["id"]),
        row["status"],
        str(row["source_id"]),
        str(row["document_id"]) if row["document_id"] else None,
        row["document_count"],
        row["chunk_count"],
        message,
    )
