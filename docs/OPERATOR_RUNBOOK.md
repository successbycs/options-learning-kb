# T480 operator runbook

## Boundaries

`options-learning-kb` is an application repository. It does not start PostgreSQL, pgvector, Ollama, or n8n, and it does not accept MP4/video files. The only accepted input is reviewed, UTF-8 Markdown created by `mp4-to-transcript`.

Course transcripts are private. Upload them through the Source Registry or hold them in an ignored local directory; never place them in this repository, issue tracker, logs, prompts to cloud models, or Git commits.

The retrieval API is read-only and returns cited passages. It cannot change risk policy, access a broker, approve/authorise a trade, or claim that a course statement establishes edge.

## Reviewed transcript contract

The Markdown front matter must provide `source_filename`, `source_sha256`, `transcript_sha256`, `lesson_title`, and `review_status`. Valid statuses are `DRAFT`, `APPROVED`, and `DISABLED`.

The `transcript_sha256` is validated against either the entire uploaded file or the canonical file with its own `transcript_sha256` front-matter line removed. The latter makes a self-referential hash practical. Generate the final canonical hash after review, insert it in the metadata, and then upload:

```bash
python3 scripts/transcript_digest.py /private/path/lesson.md
```

Every spoken passage must start with `[HH:MM:SS]`.

## Deploy on the existing T480

Before deployment, use this repository's ported read-only adapter to review the private runtime and confirm the local BGE embedding model:

```bash
python3 scripts/t480_adapter.py preflight
python3 scripts/t480_adapter.py execute --operation options_kb_preflight
python3 scripts/t480_adapter.py execute --operation ollama_bge_status
```

1. On the T480, start the existing lab services and (once) install the evaluated embedding model through the governed operations adapter:

   ```bash
   cd /home/chris/projects/cs-ai-lab-infra
   python3 scripts/t480_adapter.py execute --operation lab_services_start --approve
   python3 scripts/t480_adapter.py execute --operation ollama_embeddings_install --approve
   ```

2. Copy the reviewed migration into the shared infrastructure migration directory. Review its hash after copying. This is intentionally a separate, reviewable change because the governed adapter only applies migrations located in `cs-ai-lab-infra/postgres/migrations/`.

   ```bash
   cp /home/chris/projects/options-learning-kb/db/migrations/001_options_learning_kb.sql \
      /home/chris/projects/cs-ai-lab-infra/postgres/migrations/001_options_learning_kb.sql
   sha256sum /home/chris/projects/options-learning-kb/db/migrations/001_options_learning_kb.sql \
             /home/chris/projects/cs-ai-lab-infra/postgres/migrations/001_options_learning_kb.sql
   ```

3. Before the database mutation, run the adapter’s read-only checks. Then apply the exact reviewed file only after explicit operator approval:

   ```bash
   cd /home/chris/projects/cs-ai-lab-infra
   python3 scripts/postgres_pgvector_adapter.py preflight
   python3 scripts/postgres_pgvector_adapter.py inspect
   python3 scripts/postgres_pgvector_adapter.py vector-probe
   python3 scripts/postgres_pgvector_adapter.py apply-migration --migration-file 001_options_learning_kb.sql --approve
   ```

4. In this repository, create a private `.env` from `.env.example`; use the existing T480 database credentials and the internal Docker hostnames `postgres` and `ollama`. Set a unique retrieval token. Do not add that file to Git.

5. Build and start the two application containers. They join the existing `cs-ai-lab_internal` network; the API has no published port and the operator UI binds to loopback by default.

   ```bash
   docker compose config
   docker compose up -d --build
   docker compose ps
   ```

6. Use `http://127.0.0.1:8502` on the T480 to register a real reviewed transcript, approve it, ingest it, and run the QA pack. Uploading always creates a `DRAFT` source: operator approval is only allowed when the uploaded front matter is also `APPROVED`. Re-running ingest with the same source/transcript/model idempotency key returns the existing run without duplicate chunks.

## Database readback proof

Use a read-only select via the existing adapter workflow or a tightly scoped database session inside the existing PostgreSQL container. The acceptance readback should show:

```sql
SET search_path TO options_learning_kb, public;
SELECT s.lesson_title, s.review_status, d.document_sha256, count(c.id) AS chunks
FROM sources s
LEFT JOIN documents d ON d.source_id = s.id
LEFT JOIN chunks c ON c.document_id = d.id
GROUP BY s.lesson_title, s.review_status, d.document_sha256;

SELECT status, document_count, chunk_count, embedding_model, started_at, finished_at
FROM ingest_runs ORDER BY started_at DESC;
```

Backups remain the responsibility of the shared `cs-ai-lab-infra/scripts/backup.sh`; this application adds tables to that existing logical PostgreSQL dump. Restore testing follows the existing M3 recovery conventions, never an ad-hoc application dump.

## OptionsDecisionAgent read-only client

Attach `OptionsDecisionAgent` to `cs-ai-lab_internal`, set its private retrieval URL to `http://retrieval-api:8080`, and send `X-Options-Kb-Token`. Its only permitted call is:

```http
POST /v1/retrieval/search
X-Options-Kb-Token: <private-token>
Content-Type: application/json

{"question":"…", "source_ids": ["optional-source-uuid"], "limit": 8}
```

Treat results as cited learning material, not as a decision, policy override, trade recommendation, or evidence of trading edge.
