import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app


@pytest.mark.asyncio
async def test_health_and_config_contracts():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert "missing_required" in health.json()
        assert health.json()["database_backend"] == "sqlite+aiosqlite"
        assert health.json()["redis"]["enabled"] is False
        assert health.json()["durable_tasks"] is False
        config = await client.get("/api/config/frontend")
        assert config.status_code == 200
        assert "amap_js_key" in config.json()


@pytest.mark.asyncio
async def test_http_errors_include_request_id():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/attractions/tasks/not-found",
            headers={"X-Request-ID": "resume-evidence-123"},
        )
    assert response.status_code == 503
    assert response.headers["x-request-id"] == "resume-evidence-123"
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert response.json()["error"]["request_id"] == "resume-evidence-123"
