import json
import logging
import posixpath
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yaml
from azure.identity import DefaultAzureCredential
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContainerClient,
    generate_blob_sas,
)
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.core import blob_layout
from app.core.config import settings

logger = logging.getLogger(__name__)

_SKILL_HUB = "skills"


class BlobStorageService:
    def __init__(self):
        credential = DefaultAzureCredential()
        self.blob_service_client = BlobServiceClient(
            settings.blob_account_url, credential=credential
        )
        self.container_client: ContainerClient = (
            self.blob_service_client.get_container_client(
                settings.blob_container_name
            )
        )

    # Skill-scoped path builders — thin wrappers over app.core.blob_layout so
    # every Hub-related path convention lives in one place. These still return
    # legacy skill paths ({tid}/<skill>/...) via the compat shim in
    # blob_layout._LEGACY_HUBS until issue #35 migrates the data, then #36
    # removes the shim.

    def _tenant_prefix(self, tenant_id: str) -> str:
        return blob_layout.tenant_prefix(tenant_id)

    def _skill_prefix(self, tenant_id: str, skill_name: str) -> str:
        return blob_layout.artifact_prefix(tenant_id, _SKILL_HUB, skill_name)

    @staticmethod
    def _validate_file_path(file_path: str) -> str:
        """Normalize and validate a file path, rejecting directory traversal."""
        normalized = posixpath.normpath(file_path)
        if normalized.startswith("..") or posixpath.isabs(normalized):
            raise ValueError(f"Invalid file path: {file_path}")
        return normalized

    def _blob_path(self, tenant_id: str, skill_name: str, file_path: str) -> str:
        safe_path = self._validate_file_path(file_path)
        return blob_layout.file_path(tenant_id, _SKILL_HUB, skill_name, safe_path)

    def list_skills(self, tenant_id: str) -> list[dict]:
        prefix = self._tenant_prefix(tenant_id)
        skill_names = set()
        for blob in self.container_client.list_blobs(name_starts_with=prefix):
            # Extract skill name: tenant-id/skill-name/...
            parts = blob.name[len(prefix) :].split("/")
            if parts:
                skill_names.add(parts[0])

        skills = []
        for name in sorted(skill_names):
            skill_md_path = self._blob_path(tenant_id, name, "SKILL.md")
            try:
                blob_client = self.container_client.get_blob_client(skill_md_path)
                content = blob_client.download_blob().readall().decode("utf-8")
                frontmatter = self._parse_frontmatter(content)
                _, file_count, total_size = self._list_files_with_stats(tenant_id, name)
                props = blob_client.get_blob_properties()
                skills.append({
                    "name": name,
                    "description": frontmatter.get("description", ""),
                    "license": frontmatter.get("license", ""),
                    "compatibility": frontmatter.get("compatibility", ""),
                    "metadata": frontmatter.get("metadata", {}),
                    "file_count": file_count,
                    "total_size": total_size,
                    "created_at": props.creation_time.isoformat() if props.creation_time else "",
                    "modified_at": props.last_modified.isoformat() if props.last_modified else "",
                })
            except Exception:
                continue

        return skills

    def get_skill(self, tenant_id: str, skill_name: str) -> dict | None:
        skill_md_path = self._blob_path(tenant_id, skill_name, "SKILL.md")
        try:
            blob_client = self.container_client.get_blob_client(skill_md_path)
            content = blob_client.download_blob().readall().decode("utf-8")
            frontmatter = self._parse_frontmatter(content)
            files, file_count, total_size = self._list_files_with_stats(tenant_id, skill_name)
            props = blob_client.get_blob_properties()
            return {
                "name": skill_name,
                "description": frontmatter.get("description", ""),
                "license": frontmatter.get("license", ""),
                "compatibility": frontmatter.get("compatibility", ""),
                "metadata": frontmatter.get("metadata", {}),
                "files": files,
                "file_count": file_count,
                "total_size": total_size,
                "created_at": props.creation_time.isoformat() if props.creation_time else "",
                "modified_at": props.last_modified.isoformat() if props.last_modified else "",
            }
        except Exception:
            return None

    def _parallel_delete(self, blob_names: list[str]):
        """Delete multiple blobs in parallel using a thread pool."""
        if not blob_names:
            return

        def _del(name):
            self.container_client.get_blob_client(name).delete_blob()

        with ThreadPoolExecutor(max_workers=min(len(blob_names), 8)) as pool:
            list(pool.map(_del, blob_names))

    def create_skill(self, tenant_id: str, skill_name: str, files: dict[str, str]):
        items = [
            (self._blob_path(tenant_id, skill_name, fp), content)
            for fp, content in files.items()
        ]

        def _upload(item):
            blob_path, content = item
            self.container_client.get_blob_client(blob_path).upload_blob(
                content.encode("utf-8"), overwrite=True
            )

        with ThreadPoolExecutor(max_workers=min(len(items), 8)) as pool:
            list(pool.map(_upload, items))

    def delete_skill(self, tenant_id: str, skill_name: str):
        prefix = self._skill_prefix(tenant_id, skill_name)
        blob_names = [b.name for b in self.container_client.list_blobs(name_starts_with=prefix)]
        self._parallel_delete(blob_names)

    def read_file(self, tenant_id: str, skill_name: str, file_path: str) -> str | None:
        blob_path = self._blob_path(tenant_id, skill_name, file_path)
        try:
            blob_client = self.container_client.get_blob_client(blob_path)
            return blob_client.download_blob().readall().decode("utf-8")
        except Exception:
            return None

    def write_file(self, tenant_id: str, skill_name: str, file_path: str, content: str):
        blob_path = self._blob_path(tenant_id, skill_name, file_path)
        blob_client = self.container_client.get_blob_client(blob_path)
        blob_client.upload_blob(content.encode("utf-8"), overwrite=True)

    def delete_file(self, tenant_id: str, skill_name: str, file_path: str):
        blob_path = self._blob_path(tenant_id, skill_name, file_path)
        blob_client = self.container_client.get_blob_client(blob_path)
        blob_client.delete_blob()

    def delete_folder(self, tenant_id: str, skill_name: str, folder_path: str):
        prefix = self._blob_path(tenant_id, skill_name, folder_path.rstrip("/") + "/")
        blob_names = [b.name for b in self.container_client.list_blobs(name_starts_with=prefix)]
        self._parallel_delete(blob_names)

    def delete_files_batch(self, tenant_id: str, skill_name: str, file_paths: list[str]):
        """Delete multiple files in parallel."""
        blob_names = [self._blob_path(tenant_id, skill_name, fp) for fp in file_paths]
        self._parallel_delete(blob_names)

    def download_all_files(self, tenant_id: str, skill_name: str) -> list[tuple[str, bytes]]:
        """Return list of (relative_path, content_bytes) for all files in a skill."""
        prefix = self._skill_prefix(tenant_id, skill_name)
        blobs = list(self.container_client.list_blobs(name_starts_with=prefix))

        def _fetch(blob):
            relative_path = blob.name[len(prefix):]
            data = self.container_client.get_blob_client(blob.name).download_blob().readall()
            return (relative_path, data)

        with ThreadPoolExecutor(max_workers=min(len(blobs), 8)) as pool:
            return list(pool.map(_fetch, blobs))

    def _list_files_with_stats(self, tenant_id: str, skill_name: str) -> tuple[list[dict], int, int]:
        """List files and compute count/total_size in a single blob listing."""
        prefix = self._skill_prefix(tenant_id, skill_name)
        files = []
        total_size = 0
        for blob in self.container_client.list_blobs(name_starts_with=prefix):
            relative_path = blob.name[len(prefix):]
            files.append({
                "path": relative_path,
                "is_directory": False,
                "size": blob.size,
            })
            total_size += blob.size
        return files, len(files), total_size

    def generate_sas_urls(
        self, tenant_id: str, skill_name: str, expiry_minutes: int = 5
    ) -> list[str]:
        """Generate per-blob SAS URLs scoped to a single skill's files."""
        if not settings.blob_account_name:
            logger.warning("SAS URL skipped: BLOB_ACCOUNT_NAME is not configured")
            return []
        try:
            start = datetime.now(timezone.utc)
            expiry = start + timedelta(minutes=expiry_minutes)
            delegation_key = self.blob_service_client.get_user_delegation_key(
                key_start_time=start, key_expiry_time=expiry
            )
            prefix = self._skill_prefix(tenant_id, skill_name)
            urls: list[str] = []
            for blob in self.container_client.list_blobs(name_starts_with=prefix):
                if blob.name.endswith(".gitkeep"):
                    continue
                sas_token = generate_blob_sas(
                    account_name=settings.blob_account_name,
                    container_name=settings.blob_container_name,
                    blob_name=blob.name,
                    user_delegation_key=delegation_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=expiry,
                    start=start,
                )
                urls.append(
                    f"https://{settings.blob_account_name}.blob.core.windows.net/"
                    f"{settings.blob_container_name}/{blob.name}?{sas_token}"
                )
            return urls
        except Exception:
            logger.warning("SAS URL generation failed", exc_info=True)
            return []

    # Generic JSON-blob helpers used by non-skill hubs (MCP Hub today; future
    # Prompt/Agent Hubs will follow the same pattern). Intentionally tenant-
    # agnostic — callers compose the full path including tenant prefix.
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def write_json(self, path: str, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.container_client.get_blob_client(path).upload_blob(payload, overwrite=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def read_json(self, path: str) -> dict | None:
        try:
            raw = self.container_client.get_blob_client(path).download_blob().readall()
        except Exception:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def list_names(self, prefix: str) -> list[str]:
        return [b.name for b in self.container_client.list_blobs(name_starts_with=prefix)]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def exists(self, path: str) -> bool:
        try:
            self.container_client.get_blob_client(path).get_blob_properties()
            return True
        except Exception:
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def delete(self, path: str) -> None:
        self.container_client.get_blob_client(path).delete_blob()

    @staticmethod
    def _parse_frontmatter(content: str) -> dict:
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        try:
            return yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return {}
