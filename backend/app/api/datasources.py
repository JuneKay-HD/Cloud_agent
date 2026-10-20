"""Read-only datasource / knowledge-base registry API.

Requirement: the frontend needs the list of selectable databases and knowledge
bases, their kind labels, and readiness. Selection itself is enforced per
request by app.sources inside the chat orchestration; this endpoint never
executes SQL or retrieval — it only reflects the registry.
"""

from fastapi import APIRouter
from app.sources import list_sources, SourceError

router = APIRouter(tags=["datasources"])


@router.get("/datasources")
def datasources() -> dict:
    try:
        sources = list_sources()
    except SourceError as exc:
        return {"status": "error", "message": str(exc), "sources": []}
    return {"status": "ok", "sources": sources}