from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"


class PromptCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=NAME_PATTERN)
    description: str = Field(..., min_length=1, max_length=500)
    content: str = Field(default="", max_length=50_000)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    tags: list[str] = Field(default_factory=list)
    source: Literal["external"] = "external"
    metadata: dict[str, str] | None = None


class PromptUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., max_length=50_000)
    version: str = Field(..., min_length=1, max_length=32)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] | None = None


class PromptResponse(BaseModel):
    name: str
    description: str
    content: str
    version: str
    tags: list[str]
    source: Literal["external"] = "external"
    metadata: dict[str, str] | None = None
    created_at: str
    updated_at: str


class PromptListResponse(BaseModel):
    prompts: list[PromptResponse]
