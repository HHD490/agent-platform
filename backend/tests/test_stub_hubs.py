"""Smoke tests for hubs that graduated from 501 stubs to real CRUD.

Prompts and Agents graduated in M2. These verify the routers return real
responses (200/201) instead of 501 Coming Soon.
"""

from app.core.auth.dependencies import get_current_user
from app.core.main import app
from tests.conftest import AUTH, InMemoryStore, make_admin


def test_prompts_hub_is_live(client):
    from app.prompts.router import get_prompt_service
    from app.prompts.service import PromptService

    store = InMemoryStore()
    svc = PromptService(store=store)
    app.dependency_overrides[get_current_user] = lambda: make_admin()
    app.dependency_overrides[get_prompt_service] = lambda: svc
    try:
        resp = client.get("/api/prompts", headers=AUTH)
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        assert resp.json() == {"prompts": []}

        resp = client.post("/api/prompts", json={
            "name": "test-prompt",
            "description": "A test prompt",
            "content": "# Hello\n\nThis is a test.",
        }, headers=AUTH)
        assert resp.status_code == 201, resp.text
        doc = resp.json()
        assert doc["name"] == "test-prompt"
        assert doc["content"] == "# Hello\n\nThis is a test."
    finally:
        app.dependency_overrides.clear()


def test_agents_hub_is_live(client):
    from app.agents.router import get_agent_service
    from app.agents.service import AgentService

    store = InMemoryStore()
    svc = AgentService(store=store)
    app.dependency_overrides[get_current_user] = lambda: make_admin()
    app.dependency_overrides[get_agent_service] = lambda: svc
    try:
        resp = client.get("/api/agents", headers=AUTH)
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        assert resp.json() == {"agents": []}

        resp = client.post("/api/agents", json={
            "name": "test-agent",
            "description": "A test agent",
            "model": "gpt-4o",
            "skill_names": ["crm-opportunity"],
            "mcp_names": ["crm-mcp"],
        }, headers=AUTH)
        assert resp.status_code == 201, resp.text
        doc = resp.json()
        assert doc["name"] == "test-agent"
        assert doc["skill_names"] == ["crm-opportunity"]
    finally:
        app.dependency_overrides.clear()
