"""Alibaba Cloud Model Studio chat client."""

import asyncio
import logging
import json
import re
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger(__name__)

# Fail closed before text can become an answer, OCR evidence or a truncated
# partial result. Do not try to extract a plausible answer from mixed output.
_REASONING_TAG = re.compile(r'<\s*/?\s*(?:think|analysis|reasoning)\b[^>]*>', re.I)


def _public_response(result: dict[str, Any]) -> dict[str, Any]:
    choices = result.get('choices', [])
    if not isinstance(choices, list):
        raise BailianChatError('模型响应选项格式异常', kind='response_format')
    for choice in choices:
        message = choice.get('message') if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        for field in ('reasoning_content', 'reasoning', 'analysis'):
            message.pop(field, None)
        content = message.get('content')
        # Serializing structured content also checks text blocks and JSON values.
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        try:
            decoded = json.dumps(json.loads(text), ensure_ascii=False)
        except (ValueError, TypeError):
            decoded = ''
        if _REASONING_TAG.search(text) or _REASONING_TAG.search(decoded):
            raise BailianChatError('模型正文包含非答案内容，已阻止输出', kind='response_format')
    return result


class BailianChatError(RuntimeError):
    def __init__(self, message, *, kind="model_error", status_code=None, request_id=None, attempts=1):
        super().__init__(message)
        self.kind, self.status_code = kind, status_code
        self.request_id, self.attempts = request_id, attempts

    def diagnostic(self):
        # Never persist response bodies, prompts, keys or arbitrary exception text.
        return {"kind":self.kind,"status_code":self.status_code,
                "request_id":self.request_id,"attempts":self.attempts}


def failure_message(error, stage="任务分类"):
    kind = error.get("kind", "model_error")
    reasons = {
        "configuration":"模型配置缺失，请检查项目根目录 .env 中的 API 密钥和服务地址。",
        "authentication":"模型接口认证失败，请检查 API 密钥及访问权限。",
        "rate_limit":"模型接口请求受限，有限重试后仍未成功，请稍后重试或检查额度。",
        "timeout":"模型请求超时，请检查网络连接后重试。",
        "connection":"模型连接失败，有限重试后仍未成功，请检查网络和服务地址。",
        "service":"模型接口服务异常，有限重试后仍未成功，请稍后重试。",
        "request":"模型接口拒绝了请求，请检查模型名称、地域和请求参数。",
        "response_format":"模型返回格式异常，未能读取有效结果，请重试。",
        "classification_format":"模型分类字段不符合要求，本次未进入回答生成，请重新发送问题。",
        "empty_answer":"模型返回了空答案，请重新发送问题。",
    }
    return f"{stage}未完成：" + reasons.get(kind,"模型调用未成功，请查看执行记录后重试。")


class BailianChatIncomplete(BailianChatError):
    def __init__(self, content: str):
        super().__init__("模型输出达到长度上限")
        self.content = content


def _compatible_base(host: str) -> str:
    base = host.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    if base.endswith("/compatible-mode/v1"):
        return base
    return f"{base}/compatible-mode/v1"


async def _request(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.dashscope_api_key or not settings.bailian_host:
        raise BailianChatError("模型配置缺失", kind="configuration")
    headers = {"Authorization": f"Bearer {settings.dashscope_api_key}", "Content-Type":"application/json"}
    url = f"{_compatible_base(settings.bailian_host)}/chat/completions"
    deadline = asyncio.get_running_loop().time() + settings.model_timeout_seconds
    max_attempts = 1 + settings.model_max_retries
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        for attempt in range(1, max_attempts + 1):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise BailianChatError("请求时间预算已耗尽",kind="timeout",attempts=attempt-1)
            try:
                from app.rag.telemetry import model_request, model_usage
                model_request()
                response = await asyncio.wait_for(client.post(url,headers=headers,json=payload,timeout=remaining),remaining)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result,dict):
                    raise ValueError("response must be an object")
                model_usage(result.get('usage'))
                return _public_response(result)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                retryable = code in {408,429,500,502,503,504}
                kind = "authentication" if code in {401,403} else "rate_limit" if code==429 else "timeout" if code==408 else "service" if code>=500 else "request"
                request_id = exc.response.headers.get("x-request-id")
                # Only bounded identifiers are safe to show in logs.
                if request_id and (len(request_id)>100 or not all(c.isalnum() or c in "-_." for c in request_id)):
                    request_id = None
                error = BailianChatError(f"百炼接口 HTTP {code}",kind=kind,status_code=code,request_id=request_id,attempts=attempt)
            except (httpx.TimeoutException, asyncio.TimeoutError):
                retryable = True
                error = BailianChatError("模型请求超时",kind="timeout",attempts=attempt)
            except httpx.HTTPError:
                retryable = True
                error = BailianChatError("模型连接异常",kind="connection",attempts=attempt)
            except ValueError:
                retryable = False
                error = BailianChatError("模型接口响应不是有效 JSON 对象",kind="response_format",attempts=attempt)
            logger.warning("bailian_call_failed %s",error.diagnostic())
            delay = min(0.5 * 2**(attempt-1), 2)
            if not retryable or attempt==max_attempts or deadline-asyncio.get_running_loop().time() <= delay:
                raise error
            await asyncio.sleep(delay)


async def chat_json(system_prompt: str, user_payload: dict[str, Any], *, max_tokens: int = 2048) -> dict[str, Any]:
    settings = get_settings()
    payload = {
        "model": settings.chat_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = await _request(payload)
    try:
        content = data["choices"][0]["message"]["content"]
        if data['choices'][0].get('finish_reason') == 'length':
            raise BailianChatIncomplete(content or '')
        value = json.loads(content)
        if not isinstance(value, dict):
            raise TypeError('JSON object required')
        return value
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise BailianChatError("模型未返回合法 JSON", kind="response_format") from exc


async def chat_text(system_prompt: str, user_prompt: str) -> str:
    return await chat_messages(system_prompt, [{"role": "user", "content": user_prompt}])


async def chat_messages(system_prompt: str, messages: list[dict[str, str]]) -> str:
    """Generate a real model response with bounded conversation context."""
    settings = get_settings()
    data = await _request(
        {
            "model": settings.chat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "enable_thinking": False,
            "temperature": 0.1,
            "max_tokens": 2048,
            "stream": False,
        }
    )
    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise BailianChatError("模型未返回文本答案", kind="empty_answer")
        if data["choices"][0].get("finish_reason") == "length":
            raise BailianChatIncomplete(content.strip())
        return content.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise BailianChatError("模型未返回文本答案", kind="empty_answer") from exc
