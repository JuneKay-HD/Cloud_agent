from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    knowledge_document_ids: list[str] | None = Field(default=None, max_length=50)
    # Explicit data-source selection (requirement: never silently fall back).
    database_id: str | None = Field(default=None, max_length=80)
    knowledge_base_id: str | None = Field(default=None, max_length=80)


class QueryTask(BaseModel):
    query_scope: Literal["business", "dataset", "schema"] = "business"
    schema_plan: dict[str, Any] | None = None
    schema_context: dict[str, Any] | None = None
    clarification_context: dict[str, Any] | None = None
    intent: Literal["sql", "rag", "hybrid", "unknown"] = "unknown"
    metric_id: str | None = None
    metric_ids: list[str] = Field(default_factory=list)
    invoice_amount: dict[str, Any] | None = None
    customer_ids: list[int] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    time_range: dict[str, str] | None = None
    baseline_time_range: dict[str, str] | None = None
    comparison: str | None = None
    top_n: int | None = None
    raw_question: str = ""
    normalized_question: str = ""
    canonical_question: str = ""
    source_id: str | None = None
    element_evidence: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_fragments: list[dict[str, Any]] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    parsing_error: str | None = None

    unsupported_reason: str | None = None
    suggested_question: str | None = None
    parsing_mode: str = "rules"
    mode_reason: str = ""
    inherited_fields: list[str] = Field(default_factory=list)
    comparison_requested: bool = False
