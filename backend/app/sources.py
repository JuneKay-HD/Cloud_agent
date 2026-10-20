"""Datasource & knowledge-base registry with request-level selection.

Requests select database and knowledge-base identifiers from config/datasources.yaml.
The registry validates existence/readability and scopes SQL and retrieval
to the sources explicitly selected for the current request.

Constraints honoured here:
- No silent fallback to a default database or the whole knowledge base.
- Requests carry explicit source ids; unknown/missing ids are rejected.
- Database execution stays read-only and is always scoped to the selected db.
- Retrieval is always scoped to the selected knowledge base.
- Upload/import/create flows are NOT gated by selection (only queries/retrieval are).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)

DB_KIND = "database"
KB_KIND = "knowledge_base"

# Routes that require each kind. `hybrid` needs both; `chat` needs neither.
ROUTE_DB_KINDS = {"sql", "hybrid"}
ROUTE_KB_KINDS = {"rag", "hybrid"}

_REGISTRY_PATH = PROJECT_ROOT / "config" / "datasources.yaml"

# Request-scoped selection active for the duration of one chat request.
# (Overrides themselves are installed in app.config by switch_dataset; this
# var is only a lightweight marker used by integration helpers.)
_active_selection: contextvars.ContextVar[Selection | None] = contextvars.ContextVar(
    "active_source_selection", default=None
)


class SourceError(ValueError):
    """A source selection problem the user must resolve (missing/unknown/blocked)."""


@dataclass(frozen=True)
class DataSource:
    kind: str                 # "database" | "knowledge_base"
    source_id: str            # stable id used in requests (e.g. "chinook")
    name: str                 # display name
    detail: dict              # per-kind metadata (paths, profile, label...)

    def is_database(self) -> bool:
        return self.kind == DB_KIND

    def is_knowledge_base(self) -> bool:
        return self.kind == KB_KIND


class _Registry:
    def __init__(self, data: dict):
        self._db: dict[str, dict] = {}
        self._kb: dict[str, dict] = {}
        for kind, target in ((DB_KIND, self._db), (KB_KIND, self._kb)):
            block = data.get(kind)
            if not isinstance(block, dict):
                continue
            for source_id, entry in block.items():
                if not isinstance(entry, dict):
                    raise SourceError(f"数据源 {source_id} 配置无效")
                target[str(source_id)] = dict(entry)

    def sources(self) -> list[DataSource]:
        result = []
        for kind, target in ((DB_KIND, self._db), (KB_KIND, self._kb)):
            for source_id, entry in target.items():
                result.append(DataSource(kind=kind, source_id=source_id,
                                         name=str(entry.get("label") or source_id),
                                         detail=entry))
        return result

    def get(self, kind: str, source_id: str) -> DataSource | None:
        target = self._db if kind == DB_KIND else self._kb
        entry = target.get(source_id)
        if entry is None:
            return None
        return DataSource(kind=kind, source_id=source_id,
                          name=str(entry.get("label") or source_id), detail=entry)


@dataclass(frozen=True)
class Selection:
    database: DataSource | None
    knowledge_base: DataSource | None

    def paths_for(self, settings) -> dict:
        """Resolved absolute override paths + profile knobs for this selection.

        The selected database governs the SQL side (database path, schema config,
        profile), and the selected knowledge base governs the retrieval side
        (documents / indexes / parsed / lexical coverage). In a hybrid request
        the two sides stay consistent: the KB never overwrites the DB's config or
        profile, so SQL compilation keeps using the database's schema catalog.
        """
        overrides: dict = {}
        if self.database:
            db = self.database
            overrides["database_path"] = _resolve(db.detail.get("database_path"),
                                                  settings.database_path)
            overrides["config_path"] = _resolve(db.detail.get("config_path"),
                                                settings.config_path)
            overrides["dataset_profile"] = db.detail.get("dataset_profile") or settings.dataset_profile
            overrides["demo_data_label"] = db.detail.get("demo_data_label") or settings.demo_data_label
        if self.knowledge_base:
            kb = self.knowledge_base
            if self.database is None:
                overrides["config_path"] = _resolve(kb.detail.get("config_path"), settings.config_path)
                overrides["dataset_profile"] = kb.detail.get("dataset_profile") or settings.dataset_profile
            overrides["documents_path"] = _resolve(kb.detail.get("documents_path"),
                                                   settings.documents_path)
            overrides["indexes_path"] = _resolve(kb.detail.get("indexes_path"),
                                                 settings.indexes_path)
            overrides["parsed_path"] = _resolve(kb.detail.get("parsed_path"),
                                                settings.parsed_path)
            # Retrieval knobs come from the knowledge base.
            overrides["rag_min_lexical_coverage"] = kb.detail.get("rag_min_lexical_coverage") or settings.rag_min_lexical_coverage
            if kb.detail.get("demo_data_label"):
                overrides["demo_data_label"] = kb.detail["demo_data_label"]
        return overrides


def _resolve(value, default: Path) -> Path:
    if value is None:
        return default
    path = Path(str(value))
    return PROJECT_ROOT / path if not path.is_absolute() else path


_registry: _Registry | None = None


def _load_registry() -> _Registry:
    global _registry
    if _registry is not None:
        return _registry
    if not _REGISTRY_PATH.exists():
        raise SourceError("未找到数据源配置文件 config/datasources.yaml")
    try:
        data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SourceError(f"数据源配置无法解析：{exc}") from None
    _registry = _Registry(data if isinstance(data, dict) else {})
    return _registry


def reload_registry() -> None:
    """Invalidate the cached registry (used after config changes in tests/admin)."""
    global _registry
    _registry = None


def list_sources() -> list[dict]:
    """API-friendly list: id, kind, name, accessible, reason."""
    output = []
    for source in _load_registry().sources():
        accessible, reason = _check_accessible(source)
        output.append({
            "kind": source.kind,
            "kind_label": "数据库" if source.is_database() else "知识库",
            "source_id": source.source_id,
            "name": source.name,
            "accessible": accessible,
            "reason": reason,
        })
    return output


def _check_accessible(source: DataSource) -> tuple[bool, str]:
    settings = get_settings()
    if source.is_database():
        path = _resolve(source.detail.get("database_path"), settings.database_path)
        if not path.exists() or not path.is_file():
            return False, "数据库文件不存在，请核对数据源配置"
        return True, ""
    documents = _resolve(source.detail.get("documents_path"), settings.documents_path)
    if not documents.exists() or not documents.is_dir():
        return False, "知识库正文目录不存在，请核对数据源配置"
    return True, ""


def _resolve_source(kind: str, source_id: str | None) -> DataSource:
    if source_id is None or not str(source_id).strip():
        label = "数据库" if kind == DB_KIND else "知识库"
        raise SourceError(f"请先选择{label}，再发起该类任务")
    source = _load_registry().get(kind, str(source_id).strip())
    if source is None:
        label = "数据库" if kind == DB_KIND else "知识库"
        raise SourceError(f"所选{label}不存在或不可用（{source_id}），请重新选择")
    accessible, reason = _check_accessible(source)
    if not accessible:
        label = "数据库" if kind == DB_KIND else "知识库"
        raise SourceError(f"所选{label}不可用：{reason}")
    return source


def validate_route(route: str, database_id: str | None, knowledge_base_id: str | None,
                   *, session_scoped: bool = False) -> Selection | None:
    """Validate and return the request-scoped Selection for a chat route.

    - sql: requires an accessible database.
    - rag: requires an accessible knowledge base unless the request is already
      scoped to session attachments (``session_scoped``).
    - hybrid: requires both an accessible database and a knowledge base.
    - chat/unknown: requires neither.
    Raises SourceError with a user-facing message when a required source is
    missing or unreadable; never falls back silently.
    """
    db: DataSource | None = None
    kb: DataSource | None = None
    if route in ROUTE_DB_KINDS:
        db = _resolve_source(DB_KIND, database_id)
    if route in ROUTE_KB_KINDS and not (route == "rag" and session_scoped):
        kb = _resolve_source(KB_KIND, knowledge_base_id)
    if db is None and kb is None:
        return None
    return Selection(database=db, knowledge_base=kb)


def resolve_provided(database_id: str | None, knowledge_base_id: str | None,
                     *, enforce: bool) -> Selection | None:
    """Resolve sources that the caller explicitly provided.

    When ``enforce`` is true, at least one source must be supplied for any
    non-chat use; no silent default ever applies. When false (legacy library
    callers that carry no selection fields), explicit ids are still validated
    but missing ids are accepted as "no selection".
    """
    if not database_id and not knowledge_base_id:
        if enforce:
            raise SourceError("请先选择数据库或知识库，再发起查询")
        return None
    db = _resolve_source(DB_KIND, database_id) if database_id else None
    kb = _resolve_source(KB_KIND, knowledge_base_id) if knowledge_base_id else None
    if not db and not kb:
        raise SourceError("所选数据源均不存在或不可用，请重新选择")
    return Selection(database=db, knowledge_base=kb)


@contextlib.contextmanager
def scoped_routing_sources(database_id: str | None, knowledge_base_id: str | None):
    """Read classification metadata from available choices before knowing the route.

    Invalid choices are rejected by scoped_selection when that route needs them.
    An unrelated stale database choice must not block document classification.
    """
    if not database_id and not knowledge_base_id:
        yield
        return
    available = []
    for kind, source_id in ((DB_KIND, database_id), (KB_KIND, knowledge_base_id)):
        try:
            source = _resolve_source(kind, source_id) if source_id else None
        except SourceError:
            source = None
        available.append(source)
    selection = Selection(*available) if any(available) else None
    tokens = activate(selection)
    try:
        yield
    finally:
        deactivate(tokens)


@contextlib.contextmanager
def scoped_selection(database_id: str | None = None,
                     knowledge_base_id: str | None = None,
                     *,
                     route: str | None = None,
                     session_scoped: bool = False,
                     enforce: bool = True) -> Selection | None:
    """Route-aware, request-scoped source selection.

    When ``enforce`` is true (the product HTTP path always sends distinct
    ``database_id`` / ``knowledge_base_id`` fields), the route's required kinds
    must be explicitly selected and accessible; a missing, unknown or unreadable
    source raises SourceError and execution is blocked before any tool call.

    When ``enforce`` is false (legacy library callers that omit the selection
    fields entirely, e.g. the frozen benchmark harness), explicitly provided ids
    are still validated and scoped, but absent ids are accepted so existing
    single-profile behavior is preserved.

    Installs path/profile overrides in app.config for the duration of the block;
    contextvars propagate into asyncio.to_thread, so every read-only SQL call and
    retrieval inside the block sees the selected source. Sources not required by
    the route are not installed, so config never bleeds across kinds.

    Requirements by route:
      - sql          -> database
      - rag          -> knowledge base, unless ``session_scoped`` (attachments)
      - hybrid       -> database + (knowledge base, unless session_scoped)
      - chat/unknown -> none
    """
    needs_db = route in ROUTE_DB_KINDS if route else bool(database_id)
    needs_kb = route in ROUTE_KB_KINDS if route else bool(knowledge_base_id)
    if route == "hybrid" and session_scoped:
        needs_kb = False
    if route == "rag" and session_scoped:
        needs_kb = False
    if enforce:
        if needs_db and (database_id is None or not str(database_id).strip()):
            raise SourceError("本次任务需要数据库，请先在左侧数据源面板选择数据库，再发起查询")
        if needs_kb and (knowledge_base_id is None or not str(knowledge_base_id).strip()):
            raise SourceError("本次任务需要知识库，请先在左侧数据源面板选择知识库，再发起查询")

    db = _resolve_source(DB_KIND, database_id) if needs_db and database_id else None
    kb = _resolve_source(KB_KIND, knowledge_base_id) if needs_kb and knowledge_base_id else None
    if db is None and kb is None:
        tokens = activate(None)
        try:
            yield None
        finally:
            deactivate(tokens)
        return
    selection = Selection(database=db, knowledge_base=kb)
    tokens = activate(selection)
    try:
        yield selection
    finally:
        deactivate(tokens)


def activate(selection: Selection | None):
    """Install the request-scoped source selection (paths + profile) in config.
    Returns a token; call deactivate(token) in a finally block."""
    from app.config import set_request_overrides
    token = _active_selection.set(selection)
    overrides = selection.paths_for(get_settings()) if selection is not None else None
    overrides_token = set_request_overrides(overrides)
    return token, overrides_token


def deactivate(tokens) -> None:
    from app.config import reset_request_overrides
    token, overrides_token = tokens
    reset_request_overrides(overrides_token)
    _active_selection.reset(token)


def active_selection() -> Selection | None:
    return _active_selection.get()


def override_paths(settings) -> dict:
    """Resolved path/profile overrides for the active request, or {} when none."""
    selection = _active_selection.get()
    if selection is None:
        return {}
    return selection.paths_for(settings)
