# Options Learning KB

Private, source-cited semantic retrieval for reviewed options-learning transcripts. It is a learning/research source only—not a transcript generator, video processor, broker client, trading recommender, policy engine, or proof of trading edge.

```text
approved reviewed Markdown
  → source registry → document → timestamped chunks
  → private Ollama bge-m3 embeddings → shared PostgreSQL + pgvector
  → source-scoped semantic retrieval → cited passages
```

## T480 reuse

This repository deliberately consumes, rather than recreates, `cs-ai-lab-infra`:

| Shared service/convention | Use here |
| --- | --- |
| PostgreSQL + pgvector | `options_learning_kb` schema, migration applied through the governed adapter |
| Ollama | internal `ollama:11434`, default `bge-m3` (1024 dimensions) |
| Docker network | external `cs-ai-lab_internal`; no duplicate data/model stack |
| Backups | existing compressed PostgreSQL logical dumps and M3 restore process |
| Operations adapter | fixed T480 health/model operations and reviewed SQL migration process |

The application has a loopback-only Streamlit operator UI and an internal-only, token-protected, read-only retrieval API for `OptionsDecisionAgent`.

The repository now includes its own read-only [T480 adapter](t480/README.md), ported from the shared lab. It can inspect Windows/WSL/Docker health, shared service/network state, pgvector, and local `bge-m3` availability without exposing a general remote shell.

## Delivery governance

The KB’s [evidence-first milestone controller](docs/MILESTONE_GOVERNANCE.md) prevents a milestone from closing on assertions alone. It retains raw private proof outside Git, checks its hash and freshness, and requires an independent read-back for real T480 dependency proof. The active delivery sequence is controller → shared T480 dependencies → deployment/schema → approved private ingest → retrieval QA/gap → read-only Options Decision Agent integration.

## Schema

The migration creates source, document, timestamped chunk, ingest run, retrieval QA/question-run, and unsupported-question-gap records. Source, transcript/document, and chunk SHA-256 values make changes observable; the source/transcript/model key makes ingest idempotent. The retrieval SQL function filters to `APPROVED` sources and supports an optional source UUID scope.

## Operator UI

1. **Source Registry** — upload reviewed Markdown; capture owner/permission; approve or disable.
2. **Ingest Studio** — ingest approved records and inspect source/document/chunk hashes plus run state; safe re-run does not duplicate chunks.
3. **Search** — semantic retrieval with lesson, timestamp, similarity, and original cited passage.
4. **Test Lab** — record/run retrieval QA and visible gaps for unsupported questions.

## Run it

Follow [the T480 operator runbook](docs/OPERATOR_RUNBOOK.md). The important operational constraint is that `db/migrations/001_options_learning_kb.sql` must be reviewed and applied via the shared T480 PostgreSQL adapter—not with a new database or a local database port.

`GET /healthz` is a process liveness check. `GET /readyz` verifies PostgreSQL and the configured local Ollama model without sending or returning transcript content. Uploading always creates a `DRAFT` source; retrieval requires both an `APPROVED` transcript in its front matter and a separate operator approval.

For local code checks:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## Known limits / next steps

A real reviewed transcript and access to the private T480 runtime are required to produce the requested first-ingest, retrieval, and real QA proof. This repository does not include course material, credentials, Docker volumes, or operational evidence. Once the first private lesson is approved, ingest it through the UI, add 3–5 expected source/timestamp QA questions, record an unsupported question as a gap, and capture the private evidence listed in [MVP acceptance evidence](docs/ACCEPTANCE_EVIDENCE.md).
