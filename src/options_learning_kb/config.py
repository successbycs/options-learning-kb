from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_base_url: str = "http://ollama:11434"
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = 1024
    search_limit: int = 8
    api_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("OPTIONS_KB_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("OPTIONS_KB_DATABASE_URL is required.")
        return cls(
            database_url=database_url,
            ollama_base_url=os.environ.get("OPTIONS_KB_OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/"),
            embedding_model=os.environ.get("OPTIONS_KB_EMBEDDING_MODEL", "bge-m3"),
            embedding_dimensions=int(os.environ.get("OPTIONS_KB_EMBEDDING_DIMENSIONS", "1024")),
            search_limit=int(os.environ.get("OPTIONS_KB_SEARCH_LIMIT", "8")),
            api_token=os.environ.get("OPTIONS_KB_API_TOKEN", ""),
        )
