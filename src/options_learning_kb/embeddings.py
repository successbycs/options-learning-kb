from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import httpx


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbeddingProviderError(RuntimeError):
    """The configured local embedding service could not provide a valid vector."""


class OllamaEmbeddingProvider:
    """Private Ollama embedding client; transcript content never leaves T480."""

    def __init__(self, base_url: str, model: str, dimensions: int = 1024, timeout_seconds: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": list(texts)},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise EmbeddingProviderError("Local Ollama embedding service is unavailable.") from error
        try:
            embeddings = response.json().get("embeddings")
        except (TypeError, ValueError) as error:
            raise EmbeddingProviderError("Ollama returned an invalid embedding response.") from error
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise EmbeddingProviderError("Ollama returned an unexpected embedding response.")
        try:
            result = [[float(value) for value in vector] for vector in embeddings]
        except (TypeError, ValueError) as error:
            raise EmbeddingProviderError("Ollama returned non-numeric embedding values.") from error
        for vector in result:
            if len(vector) != self.dimensions:
                raise EmbeddingProviderError(
                    f"Embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}. "
                    "Use bge-m3 with the 1024-dimensional schema or apply a deliberate migration."
                )
        return result

    def readiness_check(self) -> None:
        """Verify that the configured model is present without sending course material."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise EmbeddingProviderError("Local Ollama service is unavailable.") from error

        try:
            models = response.json().get("models")
        except (TypeError, ValueError) as error:
            raise EmbeddingProviderError("Ollama returned an invalid model response.") from error
        names = (
            {model.get("name") for model in models if isinstance(model, dict)} if isinstance(models, list) else set()
        )
        if self.model not in names and f"{self.model}:latest" not in names:
            raise EmbeddingProviderError(f"Configured Ollama embedding model is unavailable: {self.model}")
