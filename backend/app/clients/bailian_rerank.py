"""Alibaba Cloud Model Studio rerank client."""

import httpx

from app.config import get_settings


def _native_base(host: str) -> str:
    base = host.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    suffix = "/compatible-mode/v1"
    if base.endswith(suffix):
        base = base[: -len(suffix)]
    return f"{base}/api/v1"


async def rerank(query: str, documents: list[str], top_n: int = 5) -> list[int]:
    settings = get_settings()
    if not documents:
        return []
    url = f"{_native_base(settings.bailian_host)}/services/rerank/text-rerank/text-rerank"
    payload = {
        "model": settings.rerank_model,
        "input": {"query": query, "documents": documents},
        "parameters": {"top_n": min(top_n, len(documents))},
    }
    headers = {"Authorization": f"Bearer {settings.dashscope_api_key}"}
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        from app.rag.telemetry import model_request, model_usage
        model_request()
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        value=response.json()
        model_usage(value.get('usage'))
        return [item["index"] for item in value["output"]["results"]]
