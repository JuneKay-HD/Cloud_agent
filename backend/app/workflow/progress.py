"""Request-local public execution events; never model reasoning or prompts."""
import asyncio
import inspect
import time
import threading
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

event_sink = ContextVar('execution_event_sink', default=None)
event_log = ContextVar('execution_event_log', default=None)


class EventSink:
    def __init__(self, queue):
        self.queue = queue
        self.loop = asyncio.get_running_loop()
        self.thread = threading.get_ident()

    def put_nowait(self, event):
        if threading.get_ident() == self.thread:
            self.queue.put_nowait(event)
        else:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)


def emit(event):
    log = event_log.get()
    if log is not None:
        log.append(dict(event))
    sink = event_sink.get()
    if sink is not None:
        sink.put_nowait({'type': 'step', **event})


def public_status(status):
    if status in {'failed', 'knowledge_unavailable', 'unsupported'}:
        return 'failed'
    if status in {'needs_input', 'needs_clarification', 'insufficient_evidence', 'conflicting_evidence', 'partial_answer'}:
        return 'needs_input'
    if status in {'success', 'completed', 'degraded', 'skipped', None}:
        return 'completed'
    return 'needs_input'


def observed(step):
    """Instrument the actual function boundary, without exposing its input text."""
    def decorate(fn):
        def begin(args, kwargs):
            key, started = str(uuid4()), time.perf_counter()
            detail = f'调用 {fn.__name__}；输入已交由现有校验流程处理'
            descriptions = {
                'get_session': '正在读取会话记录与附件信息',
                'retrieve_evidence': '正在检索与问题相关的文档证据',
                'parse_document_with_assessment': '正在解析文档并检查可读取内容',
                'query_attachment': '正在读取所选附件并处理问题',
                'recognize_image': '正在识别图像中的文字',
            }
            detail = descriptions.get(fn.__name__, detail)
            if fn.__name__ == 'execute_sql':
                sql = args[0] if args else kwargs.get('sql', '')
                params = args[1] if len(args) > 1 else kwargs.get('params', {})
                detail = f'只读SQL：{str(sql)[:800]}；参数名称：{list(params)}'
            emit({'id': key, 'step': step, 'status': 'running', 'tool': fn.__name__,
                  'detail': detail, 'duration_ms': None})
            return key, started, detail

        def finish(key, started, input_summary, result=None, error=None):
            status = 'failed' if error else public_status(getattr(result, 'status', 'completed'))
            detail = f'{fn.__name__} 返回；状态：{getattr(result, "status", "completed")}'
            if error:
                detail = f'{fn.__name__} 中止：{type(error).__name__}'
            elif isinstance(result, dict) and 'row_count' in result:
                detail = f'只读查询返回 {result["row_count"]} 行；SQL与参数保留在执行记录'
            elif isinstance(result, dict) and 'accepted' in result:
                status = 'completed' if result['accepted'] is True else 'needs_input'
                detail = f'来源核验：{"通过" if result["accepted"] is True else "未通过"}；{result.get("reason", "原文匹配与事实支持检查完成")}'
            elif isinstance(result, dict) and 'documents' in result:
                docs = result['documents']
                ready = sum(bool(doc.get('chunks') or doc.get('pages')) for doc in docs)
                partial = sum(doc.get('parsing_status') == 'partial' for doc in docs)
                detail = f'已读取会话记录与{len(docs)}份附件信息' if docs else '已读取会话记录；当前没有已加入会话的附件'
                if ready:
                    detail += f'；{ready}份附件有可读取的解析内容'
                if partial:
                    detail += f'；{partial}份附件仅部分内容可读'
                if ready < len(docs):
                    detail += f'；{len(docs)-ready}份附件缺少可读取内容，需补充或重新解析'
                failed = len(result.get('document_assessments', []))
                if failed:
                    detail += f'；{failed}份资料的解析尚未完成，请补充可读文件'
            elif fn.__name__ == 'parse_document_with_assessment' and isinstance(result, dict):
                detail = f'文档解析完成，取得{len(result.get("pages", []))}页内容；仍需检索与来源核验'
            elif fn.__name__ == 'recognize_image' and isinstance(result, dict):
                detail = '图像文字转写完成；识别内容仍需对照原页核对'
                if not result.get('text', '').strip():
                    status = 'needs_input'
                    detail = '图像转写未取得可读取文字，请补充清晰原件'
            elif hasattr(result, 'chunks'):
                detail = (f'检索完成，取得{len(result.chunks)}个来源片段；仍需核对适用范围与引用'
                          if fn.__name__ == 'retrieve_evidence' else detail + f'；返回{len(result.chunks)}个来源片段')
                ids = list(dict.fromkeys(c.get('doc_id') for c in result.chunks if c.get('doc_id')))
                detail += f'；文档ID：{ids[:6]}'
            elif fn.__name__ == 'check_result' and isinstance(result, tuple):
                status = public_status(result[1])
                detail = f'数据结果核对：{result[1]}；有效行 {result[0].get("valid_row_count", 0)}'
            elif fn.__name__ == 'classify_conversation' and isinstance(result, tuple):
                detail = f'实际路径：{result[0].route}；{result[1]}'
            elif fn.__name__ == 'build_clarification':
                status = 'needs_input'
                detail = '已生成澄清问题，等待用户补充；未执行未明确的查询'
            emit({'id': key, 'step': step, 'status': status, 'tool': fn.__name__,
                  'input_summary': input_summary, 'detail': detail, 'duration_ms': round((time.perf_counter()-started)*1000, 2)})

        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_call(*args, **kwargs):
                key, started, input_summary = begin(args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    finish(key, started, input_summary, error=exc)
                    raise
                finish(key, started, input_summary, result)
                return result
            return async_call

        @wraps(fn)
        def sync_call(*args, **kwargs):
            key, started, input_summary = begin(args, kwargs)
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                finish(key, started, input_summary, error=exc)
                raise
            finish(key, started, input_summary, result)
            return result
        return sync_call
    return decorate
