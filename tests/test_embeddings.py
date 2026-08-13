import httpx
import pytest

from options_learning_kb.embeddings import EmbeddingProviderError, OllamaEmbeddingProvider


def test_ollama_embedding_contract(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(
            200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]}, request=httpx.Request("POST", args[0])
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaEmbeddingProvider("http://ollama:11434", "test-model", dimensions=2)

    assert provider.embed(["one", "two"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_ollama_rejects_dimension_drift(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"embeddings": [[0.1]]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(EmbeddingProviderError, match="dimension mismatch"):
        OllamaEmbeddingProvider("http://ollama:11434", "test-model", dimensions=2).embed(["one"])


def test_ollama_readiness_accepts_latest_tag(monkeypatch):
    def fake_get(*args, **kwargs):
        return httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]}, request=httpx.Request("GET", args[0]))

    monkeypatch.setattr(httpx, "get", fake_get)
    OllamaEmbeddingProvider("http://ollama:11434", "bge-m3").readiness_check()


def test_ollama_rejects_invalid_json(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=b"not-json", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(EmbeddingProviderError, match="invalid embedding response"):
        OllamaEmbeddingProvider("http://ollama:11434", "test-model", dimensions=2).embed(["one"])
