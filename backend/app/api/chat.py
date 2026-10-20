import asyncio
from time import perf_counter
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.workflow.progress import EventSink, event_sink, event_log, observed, emit, public_status

from app.schemas.task import ChatRequest
from app.schemas.response import AgentResponse
from app.workflow.orchestrator import run_agent
from app.storage.sessions import get_session, append_message, document_summary

router = APIRouter(tags=["chat"])
_busy_sessions: set[str] = set()


@router.post("/chat")
async def chat(request: ChatRequest, http_request: Request) -> AgentResponse:
    return await _chat(request, http_request)


async def _chat(request, http_request, *, streaming=False):
    started = perf_counter()
    try:
        state = get_session(request.session_id)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None
    message = request.message.strip()
    if not message:
        raise HTTPException(422, "请输入问题")
    documents = {doc["id"]: doc for doc in state["documents"]}
    if any(key not in documents for key in request.attachment_ids):
        raise HTTPException(400, "附件不属于当前对话，请重新上传")
    if request.session_id in _busy_sessions:
        raise HTTPException(409, "当前对话正在生成回答，请稍后再试")
    public_documents = []
    if request.knowledge_document_ids:
        from app.rag.indexer import get_local_index, display_source_name
        from app.api.documents import _scoped_knowledge_base
        with _scoped_knowledge_base(request.knowledge_base_id):
            sources = get_local_index().get('sources', {})
        public_documents = [{'doc_id': key, 'name': display_source_name(sources[key]['filename'])}
                            for key in dict.fromkeys(request.knowledge_document_ids) if key in sources]
    _busy_sessions.add(request.session_id)
    try:
        append_message(request.session_id, "user", message,
                       attachments=[document_summary(documents[key]) for key in request.attachment_ids],
                       knowledge_document_ids=request.knowledge_document_ids, knowledge_documents=public_documents)
        # The product frontend always sends the source-selection fields, even
        # when empty, so an unselected source must be rejected (no silent
        # default). Legacy library callers that omit both fields keep the
        # existing single-profile behaviour for the frozen benchmark harness.
        enforce_sources = bool(
            {"database_id", "knowledge_base_id"} & set(request.model_fields_set)
        )
        try:
            if streaming:
                options = {} if request.knowledge_document_ids is None else {'knowledge_document_ids': request.knowledge_document_ids}
                if enforce_sources:
                    if 'database_id' in request.model_fields_set:
                        options['database_id'] = request.database_id
                    if 'knowledge_base_id' in request.model_fields_set:
                        options['knowledge_base_id'] = request.knowledge_base_id
                    options['enforce_sources'] = True
                response = await observed('执行任务')(run_agent)(request.session_id, message, request.attachment_ids, **options)
            else:
                response = await _run_until_disconnected(
                    http_request, request.session_id, message, request.attachment_ids,
                    request.knowledge_document_ids,
                    database_id=request.database_id if 'database_id' in request.model_fields_set else None,
                    knowledge_base_id=request.knowledge_base_id if 'knowledge_base_id' in request.model_fields_set else None,
                    enforce_sources=enforce_sources,
                )
        except Exception:
            response = AgentResponse(run_id=str(uuid4()), status="failed", message="暂时无法完成请求，请稍后重试。")
        # Wall-clock processing time, including final answer generation/checks.
        # Trace steps can overlap and therefore must not be summed as a total.
        response.elapsed_ms = round((perf_counter() - started) * 1000, 3)
        if streaming:
            records = {item['id']: item for item in event_log.get() or [] if item.get('tool')}
            response.trace = [*records.values(), *response.trace]
            from app.storage.traces import save_trace
            save_trace(response.run_id, response.trace)
        append_message(request.session_id, "assistant", response.message, result=response.model_dump())
        return response
    finally:
        _busy_sessions.discard(request.session_id)


@router.post('/chat/stream')
async def chat_stream(request: ChatRequest, http_request: Request):
    async def events():
        queue = asyncio.Queue()
        token = event_sink.set(EventSink(queue))
        log_token = event_log.set([])

        async def execute():
            try:
                response = await _chat(request, http_request, streaming=True)
                # Include original check summaries, without inventing new successes.
                for index, item in enumerate(response.trace):
                    if not item.get('tool'):
                        emit({'id': f'final-{index}', **item, 'status': public_status(item.get('status'))})
                await queue.put({'type': 'result', 'result': response.model_dump()})
            except Exception as exc:
                await queue.put({'type': 'error', 'message': exc.detail if isinstance(exc, HTTPException) else '任务执行失败，请查看执行记录后重试。'})
            finally:
                await queue.put(None)

        task = asyncio.create_task(execute())
        try:
            yield json.dumps({'type': 'step', 'id': 'accepted', 'step': '接收任务', 'status': 'pending', 'detail': '等待读取会话和校验资料范围'}, ensure_ascii=False) + '\n'
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False) + '\n'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            event_sink.reset(token)
            event_log.reset(log_token)

    return StreamingResponse(events(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


async def _run_until_disconnected(http_request, session_id, message, attachment_ids, knowledge_document_ids=None,
                                  database_id=None, knowledge_base_id=None, enforce_sources=False):
    async def watch_disconnect():
        # FastAPI has already consumed the JSON body. Wait for the ASGI
        # disconnect directly so cancellation is not swallowed by a polling scope.
        while True:
            event = await http_request.receive()
            if event['type'] == 'http.disconnect':
                return

    options = {} if knowledge_document_ids is None else {"knowledge_document_ids": knowledge_document_ids}
    # Only forward source fields when the caller explicitly carried them, so
    # legacy direct calls (and their mocks) keep the previous signature.
    if enforce_sources:
        if database_id is not None:
            options['database_id'] = database_id
        if knowledge_base_id is not None:
            options['knowledge_base_id'] = knowledge_base_id
        options['enforce_sources'] = True
    agent = asyncio.create_task(run_agent(session_id, message, attachment_ids, **options))
    watcher = asyncio.create_task(watch_disconnect())
    try:
        done, _ = await asyncio.wait({agent, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if agent in done:
            return await agent
        raise asyncio.CancelledError()
    finally:
        for task in (agent, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(agent, watcher, return_exceptions=True)
