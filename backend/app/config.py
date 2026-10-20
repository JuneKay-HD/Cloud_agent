import contextvars
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Fields that hold filesystem locations and may be configured relative to the
# project root in .env / .env.example so the delivery is location-independent.
_PATH_FIELDS = (
    "database_path", "sessions_path", "documents_path", "parsed_path",
    "indexes_path", "config_path", "public_dataset_root",
)


class Settings(BaseSettings):
    dashscope_api_key: str = ""
    bailian_host: str = ""
    chat_model: str = "qwen3.7-plus"
    embedding_model: str = "text-embedding-v4"
    rerank_model: str = "qwen3.7-text-rerank"
    # Empty by default: no original pages sent for visual localization.
    visual_location_allowed_sha256: str = ""
    vision_model: str = "qwen3.7-plus"
    multimodal_embedding_model: str = "tongyi-embedding-vision-flash-2026-03-06"
    embedding_dimensions: int = 1024
    embedding_build_concurrency: int = Field(default=4, ge=1, le=16)
    embedding_requests_per_second: float = Field(default=6, gt=0, le=20)
    rag_fusion_pool: int = Field(default=100, ge=12, le=500)
    enable_rerank: bool = True
    enable_image_retrieval: bool = False
    model_timeout_seconds: int = Field(default=60, ge=1, le=300)
    model_max_retries: int = Field(default=2, ge=0, le=2)
    summary_max_batches: int = Field(default=96, ge=1, le=256)
    summary_merge_chars: int = Field(default=48000, ge=8000, le=96000)
    summary_max_reduce_levels: int = Field(default=4, ge=1, le=8)
    sql_timeout_seconds: int = 5
    max_sql_repairs: int = 1
    dataset_profile: str = "rule_demo"
    public_dataset_root: Path | None = None
    demo_data_label: str = "合成演示数据：10位客户、20张发票、4笔已确认退款；仅用于机制验证"
    enable_ocr: bool = True
    ocr_min_confidence: float = Field(default=0.5, ge=0, le=1)
    ocr_max_pages: int = Field(default=50, ge=1, le=500)
    ocr_max_tokens: int = Field(default=16000, ge=1024, le=64000)
    presentation_render_timeout_seconds: int = Field(default=90, ge=10, le=300)
    rag_min_lexical_coverage: float = 0.45
    rag_min_vector_similarity: float = 0.55
    enable_natural_answers: bool = True
    rag_use_model_selection: bool = False
    # Region side table (citation highlighting): import-time layout binding.
    # Side-table only — never part of parser_config, never invalidates indexes.
    enable_region_layout: bool = True
    # OCR detection boxes for scanned pages (rapidocr adapter). Changing page
    # content requires an index rebuild, so it stays off until a planned
    # migration enables it together with a reindex.
    enable_region_ocr: bool = False
    database_path: Path = PROJECT_ROOT / "data" / "rule_demo" / "business.db"
    sessions_path: Path = PROJECT_ROOT / "data" / "sessions" / "conversations.db"
    documents_path: Path = PROJECT_ROOT / "data" / "rule_demo" / "documents"
    parsed_path: Path = PROJECT_ROOT / "data" / "rule_demo" / "parsed"
    indexes_path: Path = PROJECT_ROOT / "data" / "rule_demo" / "indexes"
    config_path: Path = PROJECT_ROOT / "config"

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _resolve_relative_paths(self):
        """Make the delivery location-independent: resolve path settings that
        are configured relative to the project root, while keeping absolute
        paths untouched."""
        for name in _PATH_FIELDS:
            value = getattr(self, name, None)
            if value is None:
                continue
            path = Path(value)
            if not path.is_absolute():
                setattr(self, name, PROJECT_ROOT / path)
        return self

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


@lru_cache
def _base_settings() -> Settings:
    return Settings()


# Request-scoped overrides: set by app.sources around each chat request when the
# caller supplied explicit database_id / knowledge_base_id. The contextvar is
# copied into worker threads by asyncio.to_thread / run_in_executor, so read-only
# SQL and index reads inside a request see the same selected source.
_request_overrides: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "request_path_overrides", default=None
)


def set_request_overrides(overrides: dict | None):
    """Install per-request path/profile overrides; returns the prior token."""
    if overrides:
        overrides = {k: Path(v) if k.endswith("_path") else v for k, v in overrides.items()}
    return _request_overrides.set(overrides)


def reset_request_overrides(token) -> None:
    _request_overrides.reset(token)


def get_settings() -> Settings:
    """Settings for the current context; returns a merged copy when a chat
    request carries explicit source selection, otherwise the cached base."""
    base = _base_settings()
    overrides = _request_overrides.get()
    if not overrides:
        return base
    return base.model_copy(update=overrides)


# Keep the historical cache-clear contract used by test isolation fixtures.
get_settings.cache_clear = _base_settings.cache_clear  # type: ignore[attr-defined]