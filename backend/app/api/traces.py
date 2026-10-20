from fastapi import APIRouter, HTTPException

from app.storage.traces import get_trace as load_trace

router = APIRouter(tags=["traces"])


@router.get("/runs/{run_id}/trace")
def get_trace(run_id: str) -> dict[str, object]:
    events = load_trace(run_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {"run_id": run_id, "events": events}
