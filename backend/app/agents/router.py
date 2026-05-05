from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.models import AgentCreateRequest, AgentListResponse, AgentResponse, AgentUpdateRequest
from app.agents.service import AgentAlreadyExists, AgentNotFound, AgentService
from app.core.auth.dependencies import UserInfo, require_admin, require_any_role
from app.skills.service import BlobStorageService

router = APIRouter(prefix="/api/agents", tags=["agents"])

_blob_service: BlobStorageService | None = None
_agent_service: AgentService | None = None


def get_agent_service() -> AgentService:
    global _blob_service, _agent_service
    if _agent_service is None:
        if _blob_service is None:
            _blob_service = BlobStorageService()
        _agent_service = AgentService(store=_blob_service)
    return _agent_service


@router.get("", response_model=AgentListResponse)
def list_agents(
    user: UserInfo = Depends(require_any_role),
    svc: AgentService = Depends(get_agent_service),
) -> AgentListResponse:
    docs = svc.list(tenant_id=user.tenant_id)
    return AgentListResponse(agents=[AgentResponse(**d) for d in docs])


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(
    req: AgentCreateRequest,
    user: UserInfo = Depends(require_admin),
    svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    try:
        doc = svc.create(tenant_id=user.tenant_id, request_data=req.model_dump())
    except AgentAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An agent named '{req.name}' already exists in this tenant",
        )
    return AgentResponse(**doc)


@router.get("/{name}", response_model=AgentResponse)
def get_agent(
    name: str,
    user: UserInfo = Depends(require_any_role),
    svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    try:
        doc = svc.get(tenant_id=user.tenant_id, name=name)
    except AgentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
    return AgentResponse(**doc)


@router.put("/{name}", response_model=AgentResponse)
def update_agent(
    name: str,
    req: AgentUpdateRequest,
    user: UserInfo = Depends(require_admin),
    svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    try:
        doc = svc.update(tenant_id=user.tenant_id, name=name, patch=req.model_dump())
    except AgentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
    return AgentResponse(**doc)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(
    name: str,
    user: UserInfo = Depends(require_admin),
    svc: AgentService = Depends(get_agent_service),
) -> None:
    try:
        svc.delete(tenant_id=user.tenant_id, name=name)
    except AgentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
