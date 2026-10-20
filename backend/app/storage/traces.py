"""In-memory execution traces for the minimum demo."""

from copy import deepcopy

_TRACES: dict[str, list[dict]] = {}


def save_trace(run_id: str, events: list[dict]) -> None:
    _TRACES[run_id] = deepcopy(events)


def get_trace(run_id: str) -> list[dict] | None:
    events = _TRACES.get(run_id)
    return deepcopy(events) if events is not None else None
