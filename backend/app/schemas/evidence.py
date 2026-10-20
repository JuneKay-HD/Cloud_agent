from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    evidence_id: str
    source_type: Literal["database", "document", "image"]
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)

