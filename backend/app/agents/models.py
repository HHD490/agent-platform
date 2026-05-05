from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=NAME_PATTERN)
    description: str = Field(..., min_length=1, max_length=500)
    skill_names: list[str] = Field(default_factory=list)
    mcp_names: list[str] = Field(default_factory=list)
    prompt_name: str | None = None
    model: str = Field(default="gpt-4o", min_length=1, max_length=64)
    system_prompt: str | None = Field(default=None, max_length=50_000)
    source: Literal["external"] = "external"
    metadata: dict[str, str] | None = None


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., min_length=1, max_length=500)
    skill_names: list[str] = Field(default_factory=list)
    mcp_names: list[str] = Field(default_factory=list)
    prompt_name: str | None = None
    model: str = Field(..., min_length=1, max_length=64)
    system_prompt: str | None = Field(default=None, max_length=50_000)
    metadata: dict[str, str] | None = None


class AgentResponse(BaseModel):
    name: str
    description: str
    skill_names: list[str]
    mcp_names: list[str]
    prompt_name: str | None
    model: str
    system_prompt: str | None
    source: Literal["external"] = "external"
    metadata: dict[str, str] | None = None
    created_at: str
    updated_at: str


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
