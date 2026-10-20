"""Alibaba Cloud Model Studio text embedding client."""

from typing import Any
import asyncio
import math

import httpx

from app.config import get_settings
from app.clients.bailian_chat import _compatible_base


class BailianEmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    """Keep connections during explicit builds; validate row identity before caching."""
    async def __aenter__(self):
        self.settings = get_settings()
        if not self.settings.dashscope_api_key or not self.settings.bailian_host:
            raise BailianEmbeddingError('embedding_configuration_missing')
        self.client = httpx.AsyncClient(timeout=self.settings.model_timeout_seconds)
        self.calls = self.tokens = 0
        self.rate_lock = asyncio.Lock()
        self.next_request = 0
        self.request_gap = 1 / self.settings.embedding_requests_per_second
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def batch(self, texts):
        if not 1 <= len(texts) <= 10 or any(not isinstance(t,str) or not t.strip() for t in texts):
            raise BailianEmbeddingError('invalid_embedding_input')
        s = self.settings
        deadline = asyncio.get_running_loop().time() + s.model_timeout_seconds
        for attempt in range(s.model_max_retries + 1):
            try:
                async with self.rate_lock:
                    now = asyncio.get_running_loop().time()
                    delay = max(0,self.next_request-now)
                    if delay: await asyncio.sleep(delay)
                    self.next_request = asyncio.get_running_loop().time()+self.request_gap
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                self.calls += 1
                from app.rag.telemetry import model_request, model_usage
                model_request()
                response = await asyncio.wait_for(self.client.post(
                    f'{_compatible_base(s.bailian_host)}/embeddings',
                    headers={'Authorization': f'Bearer {s.dashscope_api_key}'},
                    json={'model':s.embedding_model,'input':texts,'dimensions':s.embedding_dimensions,'encoding_format':'float'}), remaining)
                if response.status_code in {408,429,500,502,503,504} and attempt < s.model_max_retries:
                    if response.status_code==429:
                        self.request_gap = min(1.,self.request_gap*1.5)
                        try: delay = min(20.,max(3.,float(response.headers.get('retry-after',5))))
                        except ValueError: delay = 5.
                        self.next_request = max(self.next_request,asyncio.get_running_loop().time()+delay)
                    else:
                        await asyncio.sleep(min(.5 * 2**attempt, 2))
                    continue
                if not response.is_success:
                    raise BailianEmbeddingError(f'embedding_http_{response.status_code}')
                value = response.json()
                items = value['data']
                if (not isinstance(items,list) or len(items)!=len(texts) or
                    any(type(i.get('index')) is not int for i in items) or
                    sorted(i['index'] for i in items)!=list(range(len(texts)))):
                    raise ValueError('invalid_embedding_indices')
                rows = [i['embedding'] for i in sorted(items,key=lambda i:i['index'])]
                if any(not isinstance(row,list) or len(row)!=s.embedding_dimensions or
                       any(type(x) not in (int,float) or not math.isfinite(x) for x in row) or
                       sum(x*x for x in row)<=1e-12 for row in rows):
                    raise ValueError('invalid_embedding_values')
                self.tokens += value.get('usage',{}).get('total_tokens',0)
                model_usage(value.get('usage'))
                return rows
            except (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError):
                if attempt==s.model_max_retries:
                    raise BailianEmbeddingError('embedding_timeout_or_connection') from None
            except (KeyError, TypeError, ValueError):
                raise BailianEmbeddingError('embedding_response_format') from None
        raise BailianEmbeddingError('embedding_retry_exhausted')


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    results: list[list[float]] = []
    async with EmbeddingClient() as client:
        for start in range(0, len(texts), 10):
            results.extend(await client.batch(texts[start:start+10]))
    return results
