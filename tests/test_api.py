from fastapi.testclient import TestClient

from options_learning_kb import api
from options_learning_kb.service import SearchResult


def test_health_endpoint_does_not_require_database_or_token():
    response = TestClient(api.app).get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_search_is_token_protected_and_preserves_provenance(monkeypatch):
    monkeypatch.setenv("OPTIONS_KB_DATABASE_URL", "postgresql://user:password@postgres:5432/options_kb")
    monkeypatch.setenv("OPTIONS_KB_API_TOKEN", "test-token")

    class FakeService:
        def search(self, question, source_ids, limit):
            assert question == "assignment"
            assert source_ids == ["00000000-0000-0000-0000-000000000001"]
            assert limit == 3
            return [
                SearchResult(
                    chunk_id="00000000-0000-0000-0000-000000000003",
                    source_id="00000000-0000-0000-0000-000000000001",
                    lesson_title="Assignment risk",
                    timestamp="00:03:15",
                    timestamp_seconds=195,
                    passage="Assignment can occur.",
                    similarity=0.82,
                    source_sha256="a" * 64,
                    document_sha256="b" * 64,
                    chunk_sha256="c" * 64,
                )
            ]

    api.service.cache_clear()
    monkeypatch.setattr(api, "service", lambda: FakeService())
    client = TestClient(api.app)

    response = client.post("/v1/retrieval/search", json={"question": "assignment"})
    assert response.status_code == 401

    response = client.post(
        "/v1/retrieval/search",
        headers={"X-Options-Kb-Token": "test-token"},
        json={
            "question": "assignment",
            "source_ids": ["00000000-0000-0000-0000-000000000001"],
            "limit": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()[0]["document_sha256"] == "b" * 64


def test_readiness_does_not_expose_dependency_details(monkeypatch):
    class BrokenService:
        class BrokenDatabase:
            def ping(self):
                raise RuntimeError("database password in error text")

        database = BrokenDatabase()

    monkeypatch.setattr(api, "service", lambda: BrokenService())
    response = TestClient(api.app).get("/readyz")

    assert response.status_code == 503
    assert response.json()["detail"] == "Retrieval service dependencies are unavailable."
