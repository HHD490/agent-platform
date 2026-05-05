import io
import posixpath
import tarfile
import zipfile

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.auth.dependencies import get_current_user, require_admin, require_any_role, UserInfo
from app.skills.models import SkillCreateRequest, FileWriteRequest, FileRenameRequest
from app.skills.service import BlobStorageService
from app.skills.install_token import install_token_store
from app.skills.validator import validate_skill_name, validate_frontmatter

router = APIRouter(prefix="/api/skills", tags=["skills"])

MAX_IMPORT_SIZE = 10 * 1024 * 1024  # 10 MB total
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB per file
MAX_FILE_COUNT = 100
RESERVED_SKILL_NAMES = {"import", "new", "search"}


def _check_file_path(file_path: str) -> str:
    """Validate a file path, rejecting directory traversal attempts."""
    normalized = posixpath.normpath(file_path)
    if normalized.startswith("..") or posixpath.isabs(normalized):
        raise HTTPException(status_code=400, detail=f"Invalid file path: {file_path}")
    if not normalized:
        raise HTTPException(status_code=400, detail="File path must not be empty")
    return normalized


_blob_service: BlobStorageService | None = None


def get_blob_service() -> BlobStorageService:
    global _blob_service
    if _blob_service is None:
        _blob_service = BlobStorageService()
    return _blob_service


TEMPLATES = {
    "blank": {
        "SKILL.md": "---\nname: {name}\ndescription: {description}\n{license}{metadata}---\n\n# {title}\n\nAdd your skill instructions here.\n",
        "scripts/.gitkeep": "",
        "references/.gitkeep": "",
        "assets/.gitkeep": "",
    },
    "script": {
        "SKILL.md": '---\nname: {name}\ndescription: {description}\n{license}{metadata}---\n\n# {title}\n\n## Available Scripts\n\n| Script | Description | Required Args |\n|--------|-------------|---------------|\n| `scripts/main.py` | Main script | `--input` |\n\n## Usage\n\n```bash\npython scripts/main.py --input "value"\n```\n',
        "scripts/main.py": '"""Main script for {name} skill."""\nimport argparse\n\n\ndef main():\n    parser = argparse.ArgumentParser(description="{description}")\n    parser.add_argument("--input", required=True, help="Input value")\n    args = parser.parse_args()\n    print(f"Processing: {{args.input}}")\n\n\nif __name__ == "__main__":\n    main()\n',
        "references/REFERENCE.md": "# Reference\n\nAdd detailed technical reference here.\n",
        "assets/.gitkeep": "",
    },
    "instruction": {
        "SKILL.md": "---\nname: {name}\ndescription: {description}\n{license}{metadata}---\n\n# {title}\n\n## When to Use\n\nDescribe when this skill should be activated.\n\n## Instructions\n\n1. Step one\n2. Step two\n3. Step three\n\n## Examples\n\n**Example 1:**\nInput: ...\nOutput: ...\n\n## Edge Cases\n\n- Handle case A by...\n- Handle case B by...\n",
        "references/.gitkeep": "",
        "assets/.gitkeep": "",
    },
    "mcp": {
        "SKILL.md": '---\nname: {name}\ndescription: {description}\n{license}{metadata}compatibility: Requires Python 3.10+, httpx\n---\n\n# {title}\n\n## Scripts\n\n| Script | Description |\n|--------|-------------|\n| `scripts/client.py` | API client |\n\n## Setup\n\n1. Set environment variables in `.env`\n2. Run: `python scripts/client.py --action list`\n',
        "scripts/client.py": '"""MCP/API client for {name} skill."""\nimport argparse\nimport httpx\nimport os\n\n\nAPI_BASE_URL = os.getenv("API_BASE_URL", "https://api.example.com")\nAPI_KEY = os.getenv("API_KEY", "")\n\n\ndef make_request(action: str) -> dict:\n    headers = {{"Authorization": f"Bearer {{API_KEY}}"}}\n    with httpx.Client(base_url=API_BASE_URL, headers=headers) as client:\n        resp = client.get(f"/{{action}}")\n        resp.raise_for_status()\n        return resp.json()\n\n\ndef main():\n    parser = argparse.ArgumentParser(description="{description}")\n    parser.add_argument("--action", required=True, help="API action")\n    args = parser.parse_args()\n    result = make_request(args.action)\n    print(result)\n\n\nif __name__ == "__main__":\n    main()\n',
        "references/API.md": "# API Reference\n\n## Base URL\n\n`https://api.example.com`\n\n## Authentication\n\nBearer token via `API_KEY` environment variable.\n\n## Endpoints\n\n### GET /list\n\nReturns all items.\n",
        "assets/.gitkeep": "",
    },
}


def _extract_and_validate_zip(data: bytes) -> tuple[dict[str, str], str]:
    """Extract files from a ZIP archive and return (files_dict, skill_name).

    The ZIP may contain files at the top level or inside a single root folder.
    The skill name is derived from the root folder name or from the ``name``
    field in ``SKILL.md`` frontmatter.
    """
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise HTTPException(status_code=422, detail="Uploaded file is not a valid ZIP")

    files: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        _ignored_dirs = {"__pycache__", "node_modules", ".git"}

        def _should_skip(name: str) -> bool:
            if name.endswith("/"):
                return True
            if name.startswith("__MACOSX"):
                return True
            basename = posixpath.basename(name)
            if basename.startswith("."):
                return True
            parts = name.split("/")
            if any(p in _ignored_dirs for p in parts):
                return True
            return False

        names = [n for n in zf.namelist() if not _should_skip(n)]
        if len(names) > MAX_FILE_COUNT:
            raise HTTPException(status_code=422, detail=f"ZIP contains more than {MAX_FILE_COUNT} files")

        # Detect common root folder (e.g. "crm-opportunity/SKILL.md")
        parts = [n.split("/", 1) for n in names if "/" in n]
        roots = {p[0] for p in parts}
        strip_prefix = ""
        if len(roots) == 1 and all("/" in n for n in names):
            strip_prefix = roots.pop() + "/"

        for entry in names:
            raw = zf.read(entry)
            if len(raw) > MAX_FILE_SIZE:
                raise HTTPException(status_code=422, detail=f"File '{entry}' exceeds 1 MB limit")
            rel_path = entry[len(strip_prefix):] if strip_prefix and entry.startswith(strip_prefix) else entry
            if not rel_path:
                continue
            try:
                files[rel_path] = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue  # skip binary/non-text files

    if not files:
        raise HTTPException(status_code=422, detail="ZIP archive is empty")

    if "SKILL.md" not in files:
        raise HTTPException(status_code=422, detail="ZIP must contain a SKILL.md file")

    # Derive skill name from SKILL.md frontmatter or the folder name
    frontmatter = BlobStorageService._parse_frontmatter(files["SKILL.md"])
    skill_name = frontmatter.get("name", "").strip()
    if not skill_name and strip_prefix:
        skill_name = strip_prefix.rstrip("/")
    if not skill_name:
        raise HTTPException(status_code=422, detail="Cannot determine skill name from SKILL.md frontmatter")

    name_errors = validate_skill_name(skill_name)
    if name_errors:
        raise HTTPException(status_code=422, detail=name_errors)

    return files, skill_name


def _render_template(template_files: dict, req: SkillCreateRequest) -> dict[str, str]:
    title = req.name.replace("-", " ").title()
    license_line = f"license: {req.license}\n" if req.license else ""
    metadata_lines = ""
    if req.metadata:
        metadata_lines = "metadata:\n"
        for k, v in req.metadata.items():
            metadata_lines += f'  {k}: "{v}"\n'

    rendered = {}
    for path, content in template_files.items():
        rendered[path] = content.format(
            name=req.name,
            description=req.description,
            title=title,
            license=license_line,
            metadata=metadata_lines,
        )
    return rendered


# ---------------------------------------------------------------------------
# Read endpoints — SkillAdmin + SkillUser
# ---------------------------------------------------------------------------


@router.get("")
def list_skills(
    user: UserInfo = Depends(require_any_role),
    blob: BlobStorageService = Depends(get_blob_service),
):
    skills = blob.list_skills(user.tenant_id)
    return {"skills": skills, "total": len(skills), "page": 1, "page_size": len(skills)}


@router.get("/{name}")
def get_skill(
    name: str,
    user: UserInfo = Depends(require_any_role),
    blob: BlobStorageService = Depends(get_blob_service),
):
    skill = blob.get_skill(user.tenant_id, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill


@router.get("/{name}/files/{file_path:path}")
def read_file(
    name: str,
    file_path: str,
    user: UserInfo = Depends(require_any_role),
    blob: BlobStorageService = Depends(get_blob_service),
):
    _check_file_path(file_path)
    content = blob.read_file(user.tenant_id, name, file_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found")
    return {"path": file_path, "content": content}


@router.get("/{name}/download")
def download_skill(
    name: str,
    user: UserInfo = Depends(require_any_role),
    blob: BlobStorageService = Depends(get_blob_service),
):
    skill = blob.get_skill(user.tenant_id, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    files = blob.download_all_files(user.tenant_id, name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files:
            if path.endswith(".gitkeep"):
                continue
            zf.writestr(path, data)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@router.post("/{name}/install-token")
def create_install_token(
    name: str,
    request: Request,
    user: UserInfo = Depends(require_any_role),
    blob: BlobStorageService = Depends(get_blob_service),
):
    skill = blob.get_skill(user.tenant_id, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    token = install_token_store.create_token(user.tenant_id, name)
    tar_url = f"{request.base_url}api/skills/{name}/tar?token={token}"
    sas_urls = blob.generate_sas_urls(user.tenant_id, name)

    return {"tar_url": tar_url, "sas_urls": sas_urls, "expires_in": 300}


@router.get("/{name}/tar")
def download_skill_tar(
    name: str,
    token: str = Query(...),
    blob: BlobStorageService = Depends(get_blob_service),
):
    entry = install_token_store.validate_token(token)
    if entry is None or entry.skill_name != name:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    files = blob.download_all_files(entry.tenant_id, name)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, data in files:
            if path.endswith(".gitkeep"):
                continue
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{name}.tar.gz"'},
    )


@router.post("/{name}/validate")
def validate_skill(
    name: str,
    user: UserInfo = Depends(require_any_role),
    blob: BlobStorageService = Depends(get_blob_service),
):
    content = blob.read_file(user.tenant_id, name, "SKILL.md")
    if content is None:
        raise HTTPException(status_code=404, detail="SKILL.md not found")

    frontmatter = BlobStorageService._parse_frontmatter(content)
    result = validate_frontmatter(frontmatter)

    # Check name matches directory
    if frontmatter.get("name") != name:
        result.add_error("name", f"name '{frontmatter.get('name')}' does not match directory '{name}'")

    line_count = len(content.splitlines())
    if line_count > 500:
        result.add_warning("body", f"SKILL.md has {line_count} lines (recommended < 500)")

    return {"valid": result.valid, "errors": result.errors, "warnings": result.warnings}


# ---------------------------------------------------------------------------
# Write endpoints — SkillAdmin only
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
def create_skill(
    req: SkillCreateRequest,
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    if req.name in RESERVED_SKILL_NAMES:
        raise HTTPException(status_code=422, detail=f"Skill name '{req.name}' is reserved")

    name_errors = validate_skill_name(req.name)
    if name_errors:
        raise HTTPException(status_code=422, detail=name_errors)

    existing = blob.get_skill(user.tenant_id, req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Skill '{req.name}' already exists")

    template_files = TEMPLATES.get(req.template, TEMPLATES["blank"])
    rendered = _render_template(template_files, req)
    blob.create_skill(user.tenant_id, req.name, rendered)

    return blob.get_skill(user.tenant_id, req.name)


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_skill(
    file: UploadFile = File(...),
    overwrite: bool = Query(default=False),
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    """Import a skill from a ZIP file."""
    data = await file.read()
    if len(data) > MAX_IMPORT_SIZE:
        raise HTTPException(status_code=422, detail="ZIP file exceeds 10 MB limit")

    files, skill_name = _extract_and_validate_zip(data)

    if skill_name in RESERVED_SKILL_NAMES:
        raise HTTPException(status_code=422, detail=f"Skill name '{skill_name}' is reserved")

    existing = blob.get_skill(user.tenant_id, skill_name)
    if existing and not overwrite:
        raise HTTPException(
            status_code=409,
            detail={"message": f"Skill '{skill_name}' already exists", "skill_name": skill_name},
        )

    # Upload new files first (overwrite=True in create_skill handles existing blobs),
    # then delete stale files that exist in the old skill but not in the new one.
    old_paths = {f["path"] for f in existing.get("files", [])} if existing else set()
    blob.create_skill(user.tenant_id, skill_name, files)
    stale_paths = old_paths - set(files.keys())
    if stale_paths:
        blob.delete_files_batch(user.tenant_id, skill_name, list(stale_paths))

    return blob.get_skill(user.tenant_id, skill_name)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    name: str,
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    skill = blob.get_skill(user.tenant_id, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    blob.delete_skill(user.tenant_id, name)


@router.put("/{name}/files/{file_path:path}")
def write_file(
    name: str,
    file_path: str,
    req: FileWriteRequest,
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    _check_file_path(file_path)
    skill = blob.get_skill(user.tenant_id, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    blob.write_file(user.tenant_id, name, file_path, req.content)
    return {"path": file_path, "size": len(req.content.encode("utf-8"))}


@router.delete("/{name}/files/{file_path:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    name: str,
    file_path: str,
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    _check_file_path(file_path)
    blob.delete_file(user.tenant_id, name, file_path)


@router.post("/{name}/files/{file_path:path}/rename")
def rename_file(
    name: str,
    file_path: str,
    req: FileRenameRequest,
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    _check_file_path(file_path)
    _check_file_path(req.new_path)
    content = blob.read_file(user.tenant_id, name, file_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found")
    blob.write_file(user.tenant_id, name, req.new_path, content)
    blob.delete_file(user.tenant_id, name, file_path)
    return {"old_path": file_path, "new_path": req.new_path}


@router.delete("/{name}/folders/{folder_path:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(
    name: str,
    folder_path: str,
    user: UserInfo = Depends(require_admin),
    blob: BlobStorageService = Depends(get_blob_service),
):
    """Delete all files under a folder prefix."""
    _check_file_path(folder_path)
    blob.delete_folder(user.tenant_id, name, folder_path)
