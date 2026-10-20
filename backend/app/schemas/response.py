from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.evidence import Evidence


class AgentResponse(BaseModel):
    run_id: str
    task_type: Literal["chat", "sql", "rag", "hybrid", "unknown"] = "unknown"
    answer_source: str | None = None
    repetition: dict[str, Any] | None = None
    status: Literal[
        "needs_clarification", "completed", "insufficient_evidence", "failed", "unsupported", "partial_answer", "conflicting_evidence", "knowledge_unavailable"
    ]
    message: str
    verified_message: str | None = None
    presentation_mode: str | None = None
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    data: dict[str, Any] | None = None
    sql: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    clarification_options: dict[str, list[str]] = Field(default_factory=dict)

    query_conditions: dict[str, Any] | None = None
    sql_params: dict[str, Any] | None = None

    claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence_assessment: dict[str, Any] | None = None

    plan: list[dict[str, Any]] = Field(default_factory=list)
