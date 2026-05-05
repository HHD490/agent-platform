from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth.dependencies import UserInfo, require_admin, require_any_role
from app.prompts.models import PromptCreateRequest, PromptListResponse, PromptResponse, PromptUpdateRequest
from app.prompts.service import PromptAlreadyExists, PromptNotFound, PromptService
from app.skills.service import BlobStorageService

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

_blob_service: BlobStorageService | None = None
_prompt_service: PromptService | None = None


def get_prompt_service() -> PromptService:
    global _blob_service, _prompt_service
    if _prompt_service is None:
        if _blob_service is None:
            _blob_service = BlobStorageService()
        _prompt_service = PromptService(store=_blob_service)
    return _prompt_service


@router.get("", response_model=PromptListResponse)
def list_prompts(
    user: UserInfo = Depends(require_any_role),
    svc: PromptService = Depends(get_prompt_service),
) -> PromptListResponse:
    docs = svc.list(tenant_id=user.tenant_id)
    return PromptListResponse(prompts=[PromptResponse(**d) for d in docs])


@router.post("", response_model=PromptResponse, status_code=status.HTTP_201_CREATED)
def create_prompt(
    req: PromptCreateRequest,
    user: UserInfo = Depends(require_admin),
    svc: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    try:
        doc = svc.create(tenant_id=user.tenant_id, request_data=req.model_dump())
    except PromptAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A prompt named '{req.name}' already exists in this tenant",
        )
    return PromptResponse(**doc)


@router.get("/{name}", response_model=PromptResponse)
def get_prompt(
    name: str,
    user: UserInfo = Depends(require_any_role),
    svc: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    try:
        doc = svc.get(tenant_id=user.tenant_id, name=name)
    except PromptNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt '{name}' not found",
        )
    return PromptResponse(**doc)


@router.put("/{name}", response_model=PromptResponse)
def update_prompt(
    name: str,
    req: PromptUpdateRequest,
    user: UserInfo = Depends(require_admin),
    svc: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    try:
        doc = svc.update(tenant_id=user.tenant_id, name=name, patch=req.model_dump())
    except PromptNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt '{name}' not found",
        )
    return PromptResponse(**doc)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(
    name: str,
    user: UserInfo = Depends(require_admin),
    svc: PromptService = Depends(get_prompt_service),
) -> None:
    try:
        svc.delete(tenant_id=user.tenant_id, name=name)
    except PromptNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt '{name}' not found",
        )
