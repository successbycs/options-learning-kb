from __future__ import annotations

from functools import lru_cache
from secrets import compare_digest

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import ConfigurationError, Settings
from .db import Database
from .embeddings import EmbeddingProviderError, OllamaEmbeddingProvider
from .service import KnowledgeBaseService

app = FastAPI(title="Options Learning KB Retrieval API", version="0.1.0")


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=50)


class CitedPassage(BaseModel):
    lesson_title: str
    timestamp: str
    timestamp_seconds: int
    passage: str
    similarity: float
    citation: str
    source_id: str
    chunk_id: str
    source_sha256: str
    document_sha256: str
    chunk_sha256: str


@lru_cache
def service() -> KnowledgeBaseService:
    settings = Settings.from_env()
    settings.validate_runtime()
    return KnowledgeBaseService(
        Database(settings.database_url),
        OllamaEmbeddingProvider(settings.ollama_base_url, settings.embedding_model, settings.embedding_dimensions),
        settings.embedding_model,
        default_search_limit=settings.search_limit,
    )


def require_token(x_options_kb_token: str | None = Header(default=None)) -> None:
    token = Settings.from_env().api_token
    if not token or token.startswith("CHANGE_ME"):
        raise HTTPException(status_code=503, detail="Retrieval API token has not been configured.")
    if not x_options_kb_token or not compare_digest(x_options_kb_token, token):
        raise HTTPException(status_code=401, detail="Invalid retrieval token.")


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "options-learning-kb-retrieval"}


@app.get("/readyz")
def readiness() -> dict[str, str]:
    """Check dependencies without querying or exposing private course material."""
    try:
        kb_service = service()
        kb_service.database.ping()
        provider = kb_service.embeddings
        if isinstance(provider, OllamaEmbeddingProvider):
            provider.readiness_check()
    except Exception as error:
        # Dependency details are deliberately withheld from unauthenticated callers.
        raise HTTPException(status_code=503, detail="Retrieval service dependencies are unavailable.") from error
    return {"status": "ready", "service": "options-learning-kb-retrieval"}


@app.post("/v1/retrieval/search", response_model=list[CitedPassage], dependencies=[Depends(require_token)])
def search(request: SearchRequest) -> list[CitedPassage]:
    """Read-only retrieval only: no model-generated answer or trading action."""
    try:
        results = service().search(request.question, request.source_ids, request.limit)
    except (ConfigurationError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except EmbeddingProviderError as error:
        raise HTTPException(status_code=503, detail="Local embedding service is unavailable.") from error
    return [
        CitedPassage(
            lesson_title=item.lesson_title,
            timestamp=item.timestamp,
            timestamp_seconds=item.timestamp_seconds,
            passage=item.passage,
            similarity=item.similarity,
            citation=item.citation,
            source_id=item.source_id,
            chunk_id=item.chunk_id,
            source_sha256=item.source_sha256,
            document_sha256=item.document_sha256,
            chunk_sha256=item.chunk_sha256,
        )
        for item in results
    ]
