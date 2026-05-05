from pydantic import BaseModel, Field


class SkillCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=1024)
    template: str = Field(default="blank", pattern="^(blank|script|instruction|mcp)$")
    license: str | None = None
    metadata: dict[str, str] | None = None


class FileWriteRequest(BaseModel):
    content: str = Field(..., max_length=1_048_576)  # 1 MB


class FileRenameRequest(BaseModel):
    new_path: str = Field(..., min_length=1, max_length=1024)
