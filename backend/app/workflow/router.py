"""Route a validated task to SQL, RAG or hybrid processing."""

from app.schemas.task import QueryTask


def route_task(task: QueryTask) -> str:
    return task.intent if task.intent in {"sql", "rag", "hybrid"} else "rag"
