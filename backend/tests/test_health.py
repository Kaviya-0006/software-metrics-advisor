"""
Phase 1 tests: confirm the app boots, the DB initializes, and the two
foundation endpoints respond correctly.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "software-metrics-advisor"
