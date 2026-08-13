from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .db import Database
from .embeddings import OllamaEmbeddingProvider
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


@lru_cache
def service() -> KnowledgeBaseService:
    settings = Settings.from_env()
    return KnowledgeBaseService(
        Database(settings.database_url),
        OllamaEmbeddingProvider(settings.ollama_base_url, settings.embedding_model, settings.embedding_dimensions),
        settings.embedding_model,
    )


def require_token(x_options_kb_token: str | None = Header(default=None)) -> None:
    token = Settings.from_env().api_token
    if not token or token.startswith("CHANGE_ME"):
        raise HTTPException(status_code=503, detail="Retrieval API token has not been configured.")
    if x_options_kb_token != token:
        raise HTTPException(status_code=401, detail="Invalid retrieval token.")


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "options-learning-kb-retrieval"}


@app.post("/v1/retrieval/search", response_model=list[CitedPassage], dependencies=[Depends(require_token)])
def search(request: SearchRequest) -> list[CitedPassage]:
    """Read-only retrieval only: no model-generated answer or trading action."""
    try:
        results = service().search(request.question, request.source_ids, request.limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return [CitedPassage(
        lesson_title=item.lesson_title, timestamp=item.timestamp, timestamp_seconds=item.timestamp_seconds,
        passage=item.passage, similarity=item.similarity, citation=item.citation,
        source_id=item.source_id, chunk_id=item.chunk_id,
    ) for item in results]
