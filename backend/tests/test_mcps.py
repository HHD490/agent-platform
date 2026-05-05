"""HTTP-level tests for the MCP Hub CRUD endpoints (issue #15 — MCP-1a).

Tests exercise the public `/api/mcps` interface via TestClient. Auth is mocked
by overriding `get_current_user`, so the real role-checking closures
(`require_admin` / `require_any_role`) run against our synthesized UserInfo.
Blob storage is replaced via `get_mcp_service` override, which lets tests
inject an `McpService` backed by the shared InMemoryStore from conftest.
"""

import pytest

from app.core.auth.dependencies import get_current_user
from app.core.main import app
from tests.conftest import AUTH, InMemoryStore, make_admin, make_read_only


@pytest.fixture
def as_admin():
    """Override auth + MCP service for a SkillAdmin in tenant-a with an empty store."""
    from app.mcps.router import get_mcp_service
    from app.mcps.service import McpService

    store = InMemoryStore()
    svc = McpService(store=store)
    app.dependency_overrides[get_current_user] = lambda: make_admin()
    app.dependency_overrides[get_mcp_service] = lambda: svc
    yield store
    app.dependency_overrides.clear()


def test_admin_creates_mcp_and_sees_it_in_list(client, as_admin):
    resp = client.get("/api/mcps", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"mcps": []}

    body = {
        "name": "my-mcp",
        "display_name": "My MCP",
        "description": "Test MCP registration",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    resp = client.post("/api/mcps", json=body, headers=AUTH)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "my-mcp"
    assert created["display_name"] == "My MCP"
    assert created["source"] == "external"
    assert created["created_at"]
    assert created["updated_at"]

    resp = client.get("/api/mcps", headers=AUTH)
    assert resp.status_code == 200
    mcps = resp.json()["mcps"]
    assert len(mcps) == 1
    assert mcps[0]["name"] == "my-mcp"
    assert mcps[0]["endpoint_url"] == "https://example.com/mcp"


def test_two_tenants_with_same_slug_do_not_collide(client):
    from app.mcps.router import get_mcp_service
    from app.mcps.service import McpService

    store = InMemoryStore()
    svc = McpService(store=store)
    app.dependency_overrides[get_mcp_service] = lambda: svc

    body = {
        "name": "shared-slug",
        "display_name": "Shared",
        "description": "Both tenants pick this name.",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    try:
        app.dependency_overrides[get_current_user] = lambda: make_admin("tenant-a")
        resp_a_post = client.post("/api/mcps", json={**body, "display_name": "A's"}, headers=AUTH)
        assert resp_a_post.status_code == 201

        app.dependency_overrides[get_current_user] = lambda: make_admin("tenant-b")
        resp_b_post = client.post("/api/mcps", json={**body, "display_name": "B's"}, headers=AUTH)
        assert resp_b_post.status_code == 201

        app.dependency_overrides[get_current_user] = lambda: make_admin("tenant-a")
        list_a = client.get("/api/mcps", headers=AUTH).json()["mcps"]
        assert [m["display_name"] for m in list_a] == ["A's"]

        app.dependency_overrides[get_current_user] = lambda: make_admin("tenant-b")
        list_b = client.get("/api/mcps", headers=AUTH).json()["mcps"]
        assert [m["display_name"] for m in list_b] == ["B's"]
    finally:
        app.dependency_overrides.clear()


def test_skill_user_cannot_create_mcp(client):
    from app.mcps.router import get_mcp_service
    from app.mcps.service import McpService

    svc = McpService(store=InMemoryStore())
    app.dependency_overrides[get_current_user] = lambda: make_read_only()
    app.dependency_overrides[get_mcp_service] = lambda: svc
    try:
        body = {
            "name": "blocked-mcp",
            "display_name": "Blocked",
            "description": "SkillUser should not be able to create this.",
            "endpoint_url": "https://example.com/mcp",
            "transport": "streamable-http",
            "auth_type": "none",
        }
        resp = client.post("/api/mcps", json=body, headers=AUTH)
        assert resp.status_code == 403

        resp = client.get("/api/mcps", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"mcps": []}
    finally:
        app.dependency_overrides.clear()


def test_duplicate_slug_in_same_tenant_returns_409(client, as_admin):
    body = {
        "name": "dup-mcp",
        "display_name": "First",
        "description": "First registration.",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    assert client.post("/api/mcps", json=body, headers=AUTH).status_code == 201

    resp = client.post("/api/mcps", json={**body, "display_name": "Second"}, headers=AUTH)
    assert resp.status_code == 409

    mcps = client.get("/api/mcps", headers=AUTH).json()["mcps"]
    assert len(mcps) == 1
    assert mcps[0]["display_name"] == "First"


@pytest.mark.parametrize(
    "invalid_name",
    [
        "Uppercase",
        "-leading-hyphen",
        "trailing-",
        "has spaces",
        "under_score",
        "a" * 65,
    ],
)
def test_invalid_slug_returns_422(client, as_admin, invalid_name):
    body = {
        "name": invalid_name,
        "display_name": "X",
        "description": "desc",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    resp = client.post("/api/mcps", json=body, headers=AUTH)
    assert resp.status_code == 422, f"{invalid_name!r} should be rejected, got {resp.status_code}"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/mcp", 201),
        ("https://example.com", 201),
        ("http://localhost:3000/mcp", 201),
        ("http://127.0.0.1/mcp", 201),
        ("http://example.com/mcp", 422),
        ("ftp://example.com/mcp", 422),
        ("not-a-url", 422),
        ("http://127.0.0.2/mcp", 422),
    ],
)
def test_endpoint_url_validation(client, as_admin, url, expected):
    body = {
        "name": f"u-{abs(hash(url)) % 100000}",
        "display_name": "X",
        "description": "desc",
        "endpoint_url": url,
        "transport": "streamable-http",
        "auth_type": "none",
    }
    resp = client.post("/api/mcps", json=body, headers=AUTH)
    assert resp.status_code == expected, f"{url!r} expected {expected}, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("transport", "websocket"),
        ("transport", ""),
        ("auth_type", "basic"),
        ("auth_type", "oauth2"),
    ],
)
def test_unknown_enum_value_returns_422(client, as_admin, field, bad_value):
    body = {
        "name": "enum-test",
        "display_name": "X",
        "description": "desc",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    body[field] = bad_value
    resp = client.post("/api/mcps", json=body, headers=AUTH)
    assert resp.status_code == 422, f"{field}={bad_value!r} should be rejected"


def test_source_platform_authored_is_rejected_in_this_slice(client, as_admin):
    body = {
        "name": "src-test",
        "display_name": "X",
        "description": "desc",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
        "source": "platform_authored",
    }
    resp = client.post("/api/mcps", json=body, headers=AUTH)
    assert resp.status_code == 422

    body["source"] = "external"
    assert client.post("/api/mcps", json=body, headers=AUTH).status_code == 201


# ---------------------------------------------------------------------------
# MCP-1b — GET / PUT / DELETE /api/mcps/{name}
# ---------------------------------------------------------------------------


def _valid_body(**overrides) -> dict:
    body = {
        "name": "my-mcp",
        "display_name": "My MCP",
        "description": "First version.",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    body.update(overrides)
    return body


def test_get_mcp_returns_created_doc(client, as_admin):
    assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201

    resp = client.get("/api/mcps/my-mcp", headers=AUTH)
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["name"] == "my-mcp"
    assert doc["source"] == "external"
    assert doc["created_at"]
    assert doc["updated_at"] == doc["created_at"]


def test_get_unknown_mcp_returns_404(client, as_admin):
    resp = client.get("/api/mcps/does-not-exist", headers=AUTH)
    assert resp.status_code == 404


def test_put_updates_mutable_fields_and_bumps_updated_at(client, as_admin):
    import time

    assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201
    created = client.get("/api/mcps/my-mcp", headers=AUTH).json()

    time.sleep(0.01)

    update = {
        "display_name": "Renamed",
        "description": "Second version.",
        "endpoint_url": "https://new.example.com/mcp",
        "transport": "sse",
        "auth_type": "bearer_static",
        "metadata": {"owner": "team-a"},
    }
    resp = client.put("/api/mcps/my-mcp", json=update, headers=AUTH)
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["display_name"] == "Renamed"
    assert updated["updated_at"] != created["updated_at"]
    assert updated["name"] == "my-mcp"
    assert updated["source"] == "external"
    assert updated["created_at"] == created["created_at"]


def test_put_unknown_mcp_returns_404(client, as_admin):
    update = {
        "display_name": "X",
        "description": "X",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    resp = client.put("/api/mcps/does-not-exist", json=update, headers=AUTH)
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "extra_field",
    [
        {"name": "renamed"},
        {"source": "platform_authored"},
        {"created_at": "2099-01-01T00:00:00+00:00"},
        {"updated_at": "2099-01-01T00:00:00+00:00"},
    ],
)
def test_put_rejects_immutable_fields_in_payload(client, as_admin, extra_field):
    assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201
    update = {
        "display_name": "X",
        "description": "X",
        "endpoint_url": "https://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
        **extra_field,
    }
    resp = client.put("/api/mcps/my-mcp", json=update, headers=AUTH)
    assert resp.status_code == 422, f"{extra_field} should be rejected, got {resp.status_code}"


def test_put_endpoint_url_validation_reuses_post_rules(client, as_admin):
    assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201
    update = {
        "display_name": "X",
        "description": "X",
        "endpoint_url": "http://example.com/mcp",
        "transport": "streamable-http",
        "auth_type": "none",
    }
    resp = client.put("/api/mcps/my-mcp", json=update, headers=AUTH)
    assert resp.status_code == 422


def test_delete_removes_mcp_and_second_delete_returns_404(client, as_admin):
    assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201

    resp = client.delete("/api/mcps/my-mcp", headers=AUTH)
    assert resp.status_code == 204
    assert resp.content == b""

    assert client.get("/api/mcps/my-mcp", headers=AUTH).status_code == 404
    assert client.get("/api/mcps", headers=AUTH).json() == {"mcps": []}
    assert client.delete("/api/mcps/my-mcp", headers=AUTH).status_code == 404


def test_delete_unknown_mcp_returns_404(client, as_admin):
    assert client.delete("/api/mcps/never-existed", headers=AUTH).status_code == 404


def test_skill_user_can_get_but_cannot_put_or_delete(client):
    from app.mcps.router import get_mcp_service
    from app.mcps.service import McpService

    store = InMemoryStore()
    svc = McpService(store=store)
    app.dependency_overrides[get_mcp_service] = lambda: svc

    try:
        app.dependency_overrides[get_current_user] = lambda: make_admin()
        assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201

        app.dependency_overrides[get_current_user] = lambda: make_read_only()
        assert client.get("/api/mcps/my-mcp", headers=AUTH).status_code == 200

        update = {
            "display_name": "X",
            "description": "X",
            "endpoint_url": "https://example.com/mcp",
            "transport": "streamable-http",
            "auth_type": "none",
        }
        assert client.put("/api/mcps/my-mcp", json=update, headers=AUTH).status_code == 403
        assert client.delete("/api/mcps/my-mcp", headers=AUTH).status_code == 403

        app.dependency_overrides[get_current_user] = lambda: make_admin()
        assert client.get("/api/mcps/my-mcp", headers=AUTH).json()["display_name"] == "My MCP"
    finally:
        app.dependency_overrides.clear()


def test_get_mcp_scoped_to_tenant(client):
    from app.mcps.router import get_mcp_service
    from app.mcps.service import McpService

    store = InMemoryStore()
    svc = McpService(store=store)
    app.dependency_overrides[get_mcp_service] = lambda: svc
    try:
        app.dependency_overrides[get_current_user] = lambda: make_admin("tenant-a")
        assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201

        app.dependency_overrides[get_current_user] = lambda: make_admin("tenant-b")
        assert client.get("/api/mcps/my-mcp", headers=AUTH).status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# MCP-1c — GET /api/mcps/{name}/mcp-json
# ---------------------------------------------------------------------------


def test_mcp_json_endpoint_returns_snippet(client, as_admin):
    assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201

    resp = client.get("/api/mcps/my-mcp/mcp-json", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body == {
        "mcpServers": {
            "my-mcp": {
                "type": "streamable-http",
                "url": "https://example.com/mcp",
            }
        }
    }


def test_mcp_json_endpoint_for_bearer_auth(client, as_admin):
    body = {**_valid_body(), "auth_type": "bearer_static"}
    assert client.post("/api/mcps", json=body, headers=AUTH).status_code == 201

    resp = client.get("/api/mcps/my-mcp/mcp-json", headers=AUTH)
    assert resp.status_code == 200
    entry = resp.json()["mcpServers"]["my-mcp"]
    assert "_note" in entry
    assert "bearer" in entry["_note"].lower()


def test_mcp_json_endpoint_404_on_missing(client, as_admin):
    resp = client.get("/api/mcps/does-not-exist/mcp-json", headers=AUTH)
    assert resp.status_code == 404


def test_mcp_json_endpoint_available_to_skill_user(client):
    from app.mcps.router import get_mcp_service
    from app.mcps.service import McpService

    store = InMemoryStore()
    svc = McpService(store=store)
    app.dependency_overrides[get_mcp_service] = lambda: svc
    try:
        app.dependency_overrides[get_current_user] = lambda: make_admin()
        assert client.post("/api/mcps", json=_valid_body(), headers=AUTH).status_code == 201

        app.dependency_overrides[get_current_user] = lambda: make_read_only()
        resp = client.get("/api/mcps/my-mcp/mcp-json", headers=AUTH)
        assert resp.status_code == 200
        assert "mcpServers" in resp.json()
    finally:
        app.dependency_overrides.clear()
