from datetime import datetime, timezone
from typing import Protocol

from app.core import blob_layout

_HUB = "prompts"


class BlobJsonStore(Protocol):
    def write_json(self, path: str, data: dict) -> None: ...
    def read_json(self, path: str) -> dict | None: ...
    def list_names(self, prefix: str) -> list[str]: ...
    def exists(self, path: str) -> bool: ...
    def delete(self, path: str) -> None: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptAlreadyExists(Exception):
    pass


class PromptNotFound(Exception):
    pass


class PromptService:
    def __init__(self, store: BlobJsonStore):
        self._store = store

    def list(self, tenant_id: str) -> list[dict]:
        prefix = blob_layout.hub_prefix(tenant_id, _HUB)
        docs = []
        for path in sorted(self._store.list_names(prefix)):
            if not path.endswith("/metadata.json"):
                continue
            doc = self._store.read_json(path)
            if doc is not None:
                docs.append(doc)
        return docs

    def create(self, tenant_id: str, request_data: dict) -> dict:
        path = blob_layout.metadata_path(tenant_id, _HUB, request_data["name"])
        if self._store.exists(path):
            raise PromptAlreadyExists(request_data["name"])
        now = _now_iso()
        doc = {
            **request_data,
            "source": "external",
            "created_at": now,
            "updated_at": now,
        }
        self._store.write_json(path, doc)
        return doc

    def get(self, tenant_id: str, name: str) -> dict:
        doc = self._store.read_json(blob_layout.metadata_path(tenant_id, _HUB, name))
        if doc is None:
            raise PromptNotFound(name)
        return doc

    def update(self, tenant_id: str, name: str, patch: dict) -> dict:
        path = blob_layout.metadata_path(tenant_id, _HUB, name)
        existing = self._store.read_json(path)
        if existing is None:
            raise PromptNotFound(name)
        updated = {
            **existing,
            **patch,
            "name": existing["name"],
            "source": existing["source"],
            "created_at": existing["created_at"],
            "updated_at": _now_iso(),
        }
        self._store.write_json(path, updated)
        return updated

    def delete(self, tenant_id: str, name: str) -> None:
        path = blob_layout.metadata_path(tenant_id, _HUB, name)
        if not self._store.exists(path):
            raise PromptNotFound(name)
        self._store.delete(path)
