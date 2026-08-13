from fastapi.testclient import TestClient

from options_learning_kb.api import app


def test_health_endpoint_does_not_require_database_or_token():
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
