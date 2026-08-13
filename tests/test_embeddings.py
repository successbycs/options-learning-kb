import pytest
import httpx

from options_learning_kb.embeddings import OllamaEmbeddingProvider


def test_ollama_embedding_contract(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaEmbeddingProvider("http://ollama:11434", "test-model", dimensions=2)

    assert provider.embed(["one", "two"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_ollama_rejects_dimension_drift(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"embeddings": [[0.1]]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        OllamaEmbeddingProvider("http://ollama:11434", "test-model", dimensions=2).embed(["one"])
