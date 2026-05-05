"""HTTP-level tests for Skill Hub CRUD endpoints.

Tests exercise the public `/api/skills` interface via TestClient with auth
overridden and an in-memory skill store replacing Azure Blob Storage.
"""

import pytest

from app.core.auth.dependencies import get_current_user
from app.core.main import app
from app.skills.router import get_blob_service
from tests.conftest import AUTH, make_admin, make_read_only


class _SkillStore:
    """In-memory substitute for BlobStorageService used in Skill Hub tests."""

    def __init__(self):
        self._files: dict[tuple, str] = {}  # (tenant_id, skill_name, file_path) -> content

    def _key(self, tenant_id: str, skill_name: str, file_path: str) -> tuple:
        return (tenant_id, skill_name, file_path)

    # -- Skill-scoped operations --

    def list_skills(self, tenant_id: str) -> list[dict]:
        seen: dict[str, dict] = {}
        for (tid, name, path), content in self._files.items():
            if tid != tenant_id or path != "SKILL.md":
                continue
            frontmatter = self._parse_frontmatter(content)
            _, file_count, total_size = self._list_files_with_stats(tenant_id, name)
            seen[name] = {
                "name": name,
                "description": frontmatter.get("description", ""),
                "license": frontmatter.get("license", ""),
                "compatibility": frontmatter.get("compatibility", ""),
                "metadata": frontmatter.get("metadata", {}),
                "file_count": file_count,
                "total_size": total_size,
                "created_at": "2026-01-01T00:00:00+00:00",
                "modified_at": "2026-01-01T00:00:00+00:00",
            }
        return sorted(seen.values(), key=lambda s: s["name"])

    def get_skill(self, tenant_id: str, skill_name: str) -> dict | None:
        key = self._key(tenant_id, skill_name, "SKILL.md")
        if key not in self._files:
            return None
        content = self._files[key]
        frontmatter = self._parse_frontmatter(content)
        files, file_count, total_size = self._list_files_with_stats(tenant_id, skill_name)
        return {
            "name": skill_name,
            "description": frontmatter.get("description", ""),
            "license": frontmatter.get("license", ""),
            "compatibility": frontmatter.get("compatibility", ""),
            "metadata": frontmatter.get("metadata", {}),
            "files": files,
            "file_count": file_count,
            "total_size": total_size,
            "created_at": "2026-01-01T00:00:00+00:00",
            "modified_at": "2026-01-01T00:00:00+00:00",
        }

    def create_skill(self, tenant_id: str, skill_name: str, files: dict[str, str]):
        for fp, content in files.items():
            self._files[self._key(tenant_id, skill_name, fp)] = content

    def delete_skill(self, tenant_id: str, skill_name: str):
        to_delete = [k for k in self._files if k[0] == tenant_id and k[1] == skill_name]
        for k in to_delete:
            del self._files[k]

    def read_file(self, tenant_id: str, skill_name: str, file_path: str) -> str | None:
        return self._files.get(self._key(tenant_id, skill_name, file_path))

    def write_file(self, tenant_id: str, skill_name: str, file_path: str, content: str):
        self._files[self._key(tenant_id, skill_name, file_path)] = content

    def delete_file(self, tenant_id: str, skill_name: str, file_path: str):
        self._files.pop(self._key(tenant_id, skill_name, file_path), None)

    def delete_folder(self, tenant_id: str, skill_name: str, folder_path: str):
        prefix = folder_path.rstrip("/") + "/"
        to_delete = [
            k for k in self._files
            if k[0] == tenant_id and k[1] == skill_name and k[2].startswith(prefix)
        ]
        for k in to_delete:
            del self._files[k]

    def delete_files_batch(self, tenant_id: str, skill_name: str, file_paths: list[str]):
        for fp in file_paths:
            self._files.pop(self._key(tenant_id, skill_name, fp), None)

    def _list_files_with_stats(self, tenant_id: str, skill_name: str) -> tuple[list[dict], int, int]:
        files = []
        total_size = 0
        for (tid, name, path), content in self._files.items():
            if tid == tenant_id and name == skill_name:
                files.append({"path": path, "size": len(content.encode("utf-8"))})
                total_size += len(content.encode("utf-8"))
        return files, len(files), total_size

    @staticmethod
    def _parse_frontmatter(content: str) -> dict:
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        try:
            import yaml
            return yaml.safe_load(parts[1]) or {}
        except Exception:
            return {}


@pytest.fixture
def as_admin_skill():
    """Override auth + skill store for a SkillAdmin in tenant-a."""
    store = _SkillStore()
    app.dependency_overrides[get_current_user] = lambda: make_admin()
    app.dependency_overrides[get_blob_service] = lambda: store
    yield store
    app.dependency_overrides.clear()


def _skill_md(name: str, description: str = "A test skill") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nInstructions here.\n"


# ---------------------------------------------------------------------------
# CRUD — create / list / get / delete
# ---------------------------------------------------------------------------


def test_list_skills_empty(client, as_admin_skill):
    resp = client.get("/api/skills", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["skills"] == []
    assert body["total"] == 0


def test_create_and_list_skill(client, as_admin_skill):
    resp = client.post("/api/skills", json={
        "name": "my-skill",
        "description": "A test skill",
        "template": "blank",
    }, headers=AUTH)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "my-skill"
    assert created["description"] == "A test skill"
    assert created["file_count"] > 0

    resp = client.get("/api/skills", headers=AUTH)
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert len(skills) == 1
    assert skills[0]["name"] == "my-skill"


def test_get_skill_returns_detail(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "my-skill",
        "description": "A test skill",
        "template": "blank",
    }, headers=AUTH)

    resp = client.get("/api/skills/my-skill", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["name"] == "my-skill"
    assert "files" in resp.json()


def test_get_unknown_skill_returns_404(client, as_admin_skill):
    resp = client.get("/api/skills/no-such-skill", headers=AUTH)
    assert resp.status_code == 404


def test_duplicate_skill_returns_409(client, as_admin_skill):
    body = {"name": "dup-skill", "description": "desc", "template": "blank"}
    assert client.post("/api/skills", json=body, headers=AUTH).status_code == 201
    assert client.post("/api/skills", json=body, headers=AUTH).status_code == 409


def test_reserved_skill_name_import_is_rejected(client, as_admin_skill):
    resp = client.post("/api/skills", json={
        "name": "import", "description": "Should be rejected", "template": "blank",
    }, headers=AUTH)
    assert resp.status_code == 422
    assert "reserved" in resp.json()["detail"].lower()


def test_reserved_skill_name_search_is_rejected(client, as_admin_skill):
    resp = client.post("/api/skills", json={
        "name": "search", "description": "Should be rejected", "template": "blank",
    }, headers=AUTH)
    assert resp.status_code == 422


def test_delete_skill(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "to-delete", "description": "Will be deleted", "template": "blank",
    }, headers=AUTH)

    resp = client.delete("/api/skills/to-delete", headers=AUTH)
    assert resp.status_code == 204

    assert client.get("/api/skills/to-delete", headers=AUTH).status_code == 404


def test_delete_unknown_skill_returns_404(client, as_admin_skill):
    resp = client.delete("/api/skills/no-such", headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# File operations — read / write / delete
# ---------------------------------------------------------------------------


def test_read_file(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "my-skill", "description": "desc", "template": "blank",
    }, headers=AUTH)

    resp = client.get("/api/skills/my-skill/files/SKILL.md", headers=AUTH)
    assert resp.status_code == 200
    assert "content" in resp.json()
    assert "name: my-skill" in resp.json()["content"]


def test_read_unknown_file_returns_404(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "my-skill", "description": "desc", "template": "blank",
    }, headers=AUTH)
    resp = client.get("/api/skills/my-skill/files/no-such-file.txt", headers=AUTH)
    assert resp.status_code == 404


def test_write_file(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "my-skill", "description": "desc", "template": "blank",
    }, headers=AUTH)

    resp = client.put("/api/skills/my-skill/files/scripts/hello.py", json={
        "content": "print('hello world')"
    }, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["path"] == "scripts/hello.py"

    # Verify it can be read back
    resp = client.get("/api/skills/my-skill/files/scripts/hello.py", headers=AUTH)
    assert resp.json()["content"] == "print('hello world')"


def test_write_file_path_traversal_is_rejected(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "my-skill", "description": "desc", "template": "blank",
    }, headers=AUTH)

    # URL-encode dots to prevent HTTP client from normalizing ../ away
    resp = client.put("/api/skills/my-skill/files/%2e%2e%2fescape.md", json={
        "content": "should not work"
    }, headers=AUTH)
    assert resp.status_code == 400, f"expected 400 for path traversal, got {resp.status_code}"


def test_delete_file(client, as_admin_skill):
    client.post("/api/skills", json={
        "name": "my-skill", "description": "desc", "template": "blank",
    }, headers=AUTH)

    resp = client.delete("/api/skills/my-skill/files/SKILL.md", headers=AUTH)
    assert resp.status_code == 204

    resp = client.get("/api/skills/my-skill/files/SKILL.md", headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Role-based access — SkillUser (read-only)
# ---------------------------------------------------------------------------


def test_skill_user_cannot_create(client):
    store = _SkillStore()
    app.dependency_overrides[get_current_user] = lambda: make_read_only()
    app.dependency_overrides[get_blob_service] = lambda: store
    try:
        resp = client.post("/api/skills", json={
            "name": "blocked", "description": "nope", "template": "blank",
        }, headers=AUTH)
        assert resp.status_code == 403

        # But list and get are allowed
        resp = client.get("/api/skills", headers=AUTH)
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_skill_user_cannot_write_file(client):
    store = _SkillStore()
    store.create_skill("tenant-a", "my-skill", {"SKILL.md": _skill_md("my-skill")})
    app.dependency_overrides[get_current_user] = lambda: make_read_only()
    app.dependency_overrides[get_blob_service] = lambda: store
    try:
        resp = client.put("/api/skills/my-skill/files/test.txt", json={
            "content": "hack"
        }, headers=AUTH)
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
