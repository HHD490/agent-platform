import pytest
from fastapi.testclient import TestClient

from app.core.main import app
from app.core.auth.dependencies import UserInfo


class InMemoryStore:
    """Dict-backed substitute for Azure Blob used by all non-skill Hub services.

    Satisfies the BlobJsonStore protocol (write_json / read_json / list_names /
    exists / delete) so it can back McpService, PromptService, AgentService, etc.
    """

    def __init__(self):
        self._data: dict[str, dict] = {}

    def write_json(self, path: str, data: dict) -> None:
        self._data[path] = data

    def read_json(self, path: str) -> dict | None:
        return self._data.get(path)

    def list_names(self, prefix: str) -> list[str]:
        return [p for p in self._data if p.startswith(prefix)]

    def exists(self, path: str) -> bool:
        return path in self._data

    def delete(self, path: str) -> None:
        self._data.pop(path, None)


def make_admin(tenant_id: str = "tenant-a") -> UserInfo:
    return UserInfo(
        oid="admin-oid",
        tenant_id=tenant_id,
        name="Admin",
        email="admin@x.com",
        roles=["SkillAdmin"],
    )


def make_read_only(tenant_id: str = "tenant-a") -> UserInfo:
    return UserInfo(
        oid="user-oid",
        tenant_id=tenant_id,
        name="User",
        email="user@x.com",
        roles=["SkillUser"],
    )


AUTH = {"Authorization": "Bearer fake-token"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def valid_token_payload():
    return {
        "oid": "test-user-object-id-123",
        "preferred_username": "jiawei@example.com",
        "name": "Jiawei Chen",
        "tid": "test-tenant-id",
        "aud": "test-audience",
        "exp": 9999999999,
        "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
    }


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer fake-valid-token"}
