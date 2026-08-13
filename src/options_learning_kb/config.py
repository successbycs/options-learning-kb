from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    """Raised when required private runtime configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_base_url: str = "http://ollama:11434"
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = 1024
    search_limit: int = 8
    api_token: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.environ.get("OPTIONS_KB_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("OPTIONS_KB_DATABASE_URL is required.")
        return cls(
            database_url=database_url,
            ollama_base_url=os.environ.get("OPTIONS_KB_OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/"),
            embedding_model=os.environ.get("OPTIONS_KB_EMBEDDING_MODEL", "bge-m3"),
            embedding_dimensions=_positive_int("OPTIONS_KB_EMBEDDING_DIMENSIONS", 1024),
            search_limit=_bounded_int("OPTIONS_KB_SEARCH_LIMIT", 8, minimum=1, maximum=50),
            api_token=os.environ.get("OPTIONS_KB_API_TOKEN", ""),
        )

    def validate_runtime(self) -> None:
        database = urlparse(self.database_url)
        if database.scheme not in {"postgres", "postgresql"} or not database.hostname:
            raise ConfigurationError("OPTIONS_KB_DATABASE_URL must be a PostgreSQL connection URL.")
        ollama = urlparse(self.ollama_base_url)
        if ollama.scheme not in {"http", "https"} or not ollama.hostname:
            raise ConfigurationError("OPTIONS_KB_OLLAMA_BASE_URL must be an absolute HTTP(S) URL.")
        if not self.embedding_model.strip():
            raise ConfigurationError("OPTIONS_KB_EMBEDDING_MODEL is required.")
        if self.embedding_dimensions != 1024:
            raise ConfigurationError("The current pgvector schema requires OPTIONS_KB_EMBEDDING_DIMENSIONS=1024.")


def _positive_int(name: str, default: int) -> int:
    return _bounded_int(name, default, minimum=1, maximum=100_000)


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value
