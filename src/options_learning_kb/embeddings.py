from __future__ import annotations

from typing import Protocol, Sequence

import httpx


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


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
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": list(texts)},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("Ollama returned an unexpected embedding response.")
        result = [[float(value) for value in vector] for vector in embeddings]
        for vector in result:
            if len(vector) != self.dimensions:
                raise RuntimeError(
                    f"Embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}. "
                    "Use bge-m3 with the 1024-dimensional schema or apply a deliberate migration."
                )
        return result
