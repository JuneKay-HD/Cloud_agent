"""Coordinate query execution, uploaded documents and evidence assessment."""

import asyncio
import re
import json
import time
from uuid import uuid4
from app.workflow.progress import emit, public_status

from app.rag.answerer import GroundedAnswer, answer_from_documents
from app.rag.retriever import retrieve_evidence
from app.schemas.evidence import Evidence
from app.schemas.response import AgentResponse
from app.sql.executor import execute_sql
from app.sql.generator import UnsupportedQuery, generate_sql
from app.sql.results import check_result, describe_task, render_sql_answer
from app.sql.validator import validate_query_contract
from app.storage.sessions import get_session, update_session
from app.storage.traces import save_trace
from app.workflow.clarification import build_clarification
from app.workflow.evidence_followup import evidence_options, resolve_evidence_followup
from app.workflow.intent_parser import parse_intent
from app.workflow.language import normalize_question
from app.workflow.router import route_task
from app.clients.bailian_chat import BailianChatError, BailianChatIncomplete, failure_message
from app.config import get_settings
from app.schemas.task import QueryTask
from app.workflow.conversation import classify_conversation, answer_conversation, cancels_pending, document_scope, public_retrieval_question, text_transformation


def _event(name, started, detail, status="success"):
    event = {
        "id": str(uuid4()),
        "step": name,
        "status": status,
        "detail": detail,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    emit({**event, "status": public_status(status)})
    return event


def _route_source_requirements(route: str, attachment_ids: list[str],
                               public_ids: list[str] | None) -> tuple[bool, bool]:
    """Map a classified route to (needs_database, needs_public_knowledge_base).

    - sql: database only.
    - rag: public knowledge base, unless the retrieval is limited to session
      attachments (attachment ids present and no public document selection).
    - hybrid: database plus (public knowledge base or session attachments).
    - chat / unknown: neither.
    """
    if route in {"sql"}:
        return True, False
    if route in {"hybrid"}:
        return True, not bool(attachment_ids) or public_ids is not None
    if route == "rag":
        return False, (public_ids is not None or not bool(attachment_ids))
    return False, False


from app.sources import SourceError, scoped_selection, scoped_routing_sources, active_selection  # noqa: E402


def _source_denied_response(started, exc: SourceError):
    """Build the user-facing response for a missing/unknown source."""
    return AgentResponse(
        run_id=str(uuid4()),
        task_type="unknown",
        status="needs_clarification",
        message=str(exc),
        trace=[_event("核对数据源", started, str(exc), "needs_input")],
    )


def _document_evidence(
    answer: GroundedAnswer,
    *,
    session_id: str | None = None,
    session_scoped: bool = False,
) -> list[Evidence]:
    evidence = []
    for index, chunk in enumerate(answer.chunks, 1):
        metadata = {
            "citation": index,
            "section": chunk.get("section"),
            "page_no": chunk.get("page_no"),
            "score": chunk.get("score"),
            "source_hash": chunk.get("source_hash"),
            "start_char": chunk.get("start_char"),
            "end_char": chunk.get("end_char"),
            "extraction_method": chunk.get("extraction_method", "text"),
            "ocr_min_confidence": chunk.get("ocr_min_confidence"),
            "ocr_validation": chunk.get('ocr_validation'),
            "parsing_warnings": chunk.get('parsing_warnings', []),
            "bm25_score": chunk.get("bm25_score"),
              "vector_score": chunk.get("vector_score"),
              "fusion_method": chunk.get("fusion_method"),
              "candidate_only": bool(answer.assessment.get('candidate_sources_only')),
            "filename": chunk.get("filename", chunk.get("title", "附件")),
            "doc_id": chunk.get('doc_id'),
            "source_metadata": chunk.get("source_metadata", {}),
            "quotes": [
                claim["quote"]
                for claim in answer.claims
                if claim["evidence_id"] == chunk["chunk_id"]
            ],
        }
        if session_scoped and session_id:
            metadata["source_url"] = (
                f"/api/sessions/{session_id}/documents/{chunk['doc_id']}/source"
                f"?chunk_id={chunk['chunk_id']}"
            )
        else:
            selection = active_selection()
            kb_query = ""
            if selection and selection.knowledge_base:
                kb_query = f"&knowledge_base_id={selection.knowledge_base.source_id}"
            metadata["source_url"] = (
                f"/api/documents/{chunk['doc_id']}/source?chunk_id={chunk['chunk_id']}{kb_query}"
            )
            metadata["file_url"] = (
                f"/api/documents/{chunk['doc_id']}/file?knowledge_base_id={selection.knowledge_base.source_id}"
                if selection and selection.knowledge_base
                else f"/api/documents/{chunk['doc_id']}/file"
            )
        evidence.append(
            Evidence(
                evidence_id=chunk["chunk_id"],
                source_type="document",
                title=chunk.get("title", "文档"),
                content=chunk["text"],
                metadata=metadata,
            )
        )
    return evidence


async def _answer_with_evidence(
    question,
    trace,
    *,
    entity_ids=None,
    time_range=None,
    version=None,
    extra_chunks=None,
    reply_preferences=None,
    knowledge_document_ids=None,
):
    started = time.perf_counter()
    try:
        scope_options = {} if knowledge_document_ids is None else {"knowledge_document_ids": knowledge_document_ids}
        retrieval = await retrieve_evidence(
            question,
            entity_ids=entity_ids,
            time_range=time_range,
            version=version,
            extra_chunks=extra_chunks,
            **scope_options,
        )
        trace.append(
            _event(
                "检索文档",
                started,
                f"{retrieval.mode}；相关候选{retrieval.candidate_count}个，"
                f"适用片段{len(retrieval.chunks)}个，因实体/时间/版本/类别排除{len(retrieval.rejected)}个；"
                f"重排序：{ {'applied': '已执行', 'fallback': '失败后降级', 'not_requested': '未调用'}[retrieval.rerank_status]}",
            )
        )
        if retrieval.query_expansion_status != 'not_requested' or retrieval.category:
            trace.append(_event('理解检索语义与文档范围', time.perf_counter(),
                f"跨语言表达：{retrieval.query_expansion_status}；指定类别：{retrieval.category or '未限定'}；"
                f"实际检索表达：{retrieval.search_queries}"))
        if retrieval.diagnostics:
            trace.append(_event('记录检索范围',time.perf_counter(),json.dumps(retrieval.diagnostics,ensure_ascii=False)))
        started = time.perf_counter()
        answer = await answer_from_documents(question, retrieval, reply_preferences=reply_preferences)
        if answer.assessment.get('answer_mode', '').startswith('document_summary_'):
            trace.append(_event('浏览文档与提炼摘要', started,
                f"按正文顺序读取{answer.assessment.get('read_chunk_count', 0)}个片段；"
                f"完成{len(answer.assessment['summary_batches'])}批提炼与独立核验；"
                f"最终摘要：{answer.assessment.get('answer_mode')}；"
                f"{('失败阶段：' + answer.assessment['failure_stage'] + '，原因：' + answer.assessment.get('rejection_reason', '')) if answer.assessment.get('failure_stage') else '引用与摘要已核验'}", answer.status))
        detail = (
            f"已核对原文引用{len(answer.claims)}条；"
            f"缺失要素{len(answer.assessment['missing_aspects'])}项；"
            f"口径冲突{len(answer.assessment['conflicts'])}项"
        )
        if answer.assessment.get('context_chunks_added'):
            detail += f"；从同一来源补齐相邻原文{answer.assessment['context_chunks_added']}个片段"
        if answer.assessment.get('repair_attempted') or retrieval.answer_diagnostics.get('repair_attempted'):
            detail += '；依据核验反馈修正回答一次并重新核验'
        if retrieval.answer_rejection:
            detail += f'；未完成原因：{retrieval.answer_rejection}'
        trace.append(_event("证据充分性与一致性", started, detail, answer.status))
        from app.rag.assessment_output import compact_rejection_details
        compact_rejection_details(answer.assessment)
        # 暴露本轮到后续续问可继承的检索范围：完整候选集、展示给用户的已采纳
        # 子集、核验通过的引用chunk，以及本次实际使用的知识库。全部来自真实检索。
        selection = active_selection()
        kb_id = None
        if selection and selection.knowledge_base:
            kb_id = selection.knowledge_base.source_id
        retrieval_scope = {
            'candidate_source_ids': sorted({c.get('doc_id') for c in retrieval.chunks if c.get('doc_id')}),
            'candidate_displayed_source_ids': sorted({c.get('doc_id') for c in answer.chunks if c.get('doc_id')}),
            'verified_chunk_ids': [claim.get('evidence_id') for claim in answer.claims if claim.get('evidence_id')],
            'retrieval_mode': retrieval.mode,
            'knowledge_status': retrieval.knowledge_status,
            'kb_id': kb_id,
        }
        answer.assessment.setdefault('retrieval_scope', retrieval_scope)
        return answer
    except Exception as exc:
        trace.append(_event("知识库不可用", started, type(exc).__name__, "failed"))
        return GroundedAnswer(
            "knowledge_unavailable",
            "知识库读取或证据校验失败，暂时无法核对文档答案。",
            assessment={
                "warnings": ["知识库读取或校验异常"],
                "missing_aspects": [],
                "conflicts": [],
                "error_type": type(exc).__name__,
            },
        )


async def run_agent(
    session_id: str,
    message: str,
    attachment_ids: list[str] | None = None,
    *,
    knowledge_document_ids: list[str] | None = None,
    database_id: str | None = None,
    knowledge_base_id: str | None = None,
    enforce_sources: bool = False,
) -> AgentResponse:
    state = get_session(session_id)
    question = message.strip()
    context = state.get('last_document_context') or {}
    subjects = set(re.findall(r'销售额|发票数量|客户数量|净收入|退款金额',context.get('question','')))
    if (len(subjects)>1 and re.search(r'这个数字|该数字|这个指标|该指标',question)
            and not re.search(r'销售额|发票数量|客户数量|净收入|退款金额',question)):
        response = AgentResponse(run_id=str(uuid4()),task_type='rag',status='needs_clarification',
            message='请明确“这个数字/该指标”指的是哪个指标，避免引用错误口径。',
            trace=[_event('核对会话指代',time.perf_counter(),'上下文有多个指标，未执行检索','needs_input')])
        update_session(session_id,last_route='rag',last_result=response.model_dump())
        save_trace(response.run_id,response.trace)
        return response
    from app.workflow.response_preferences import reply_contract, split_reply_request
    contract = reply_contract(question, state)
    from app.workflow.document_routing import terminology_context
    contract['terminology'] = terminology_context(state)
    if contract.pop('persist', False):
        update_session(session_id, reply_preferences=contract)
    update_session(session_id, active_reply_contract=contract)
    state['active_reply_contract'] = contract
    from app.workflow.reply_context import resolve_requery, prepare_history, analyze_repeat, decorate_answer
    original_question = question
    question, style_clauses = (question, []) if text_transformation(question) else split_reply_request(question)
    resolved = resolve_requery(question, state)
    if resolved is None:
        return AgentResponse(run_id=str(uuid4()), status="needs_clarification",
                             message="请您明确需要重新查询的问题，前一轮尚未形成可复查的业务结果。")
    question = resolved
    from app.workflow.presentation import present_answer, STYLE_FOLLOWUP
    previous = state.get('last_result') or {}
    if not attachment_ids and knowledge_document_ids is None and STYLE_FOLLOWUP.fullmatch(question) and previous.get('status') in {'completed','partial_answer'} and previous.get('task_type') in {'sql','rag','hybrid'}:
        response = AgentResponse.model_validate(previous)
        previous_id = response.run_id
        response.run_id = str(uuid4())
        response.message = response.verified_message or response.message
        response.presentation_mode = None
        response.repetition = {**(response.repetition or {}), "relation":"style_only",
                               "fresh_query":False, "fresh_retrieval":False,
                               "execution_question":resolve_requery("再查一次", state)}
        response.trace = [_event('复用已核验结果', time.perf_counter(), f'仅调整表达，未重新查询；原运行编号：{previous_id}')]
        response = await present_answer(question, response, history_context={'reply_contract':contract})
        update_session(session_id, last_result=response.model_dump())
        save_trace(response.run_id, response.trace)
        return response
    cancelled = cancels_pending(question)
    if cancelled:
        update_session(session_id, pending_message=None, pending_plan=None, pending_plan_attachment_ids=[], pending_evidence=None,
                       last_task=None, last_document_context=None, cancelled_task=True,
                       routing_history_start=max(0, len(state.get("messages", [])) - 1))
        state = get_session(session_id)
    started = time.perf_counter()
    from app.rag.source_scope import public_document_scope
    from app.workflow.conversation import ConversationRoute
    with scoped_routing_sources(database_id, knowledge_base_id):
        public_ids, public_error = public_document_scope(question, state, attachment_ids or [], knowledge_document_ids, knowledge_base_id=knowledge_base_id)
    if public_error:
        response = AgentResponse(run_id=str(uuid4()), task_type='rag', status='needs_clarification',
            message=public_error, trace=[_event('核对资料范围', started, public_error, 'needs_input')])
        update_session(session_id, last_result=response.model_dump(), last_route='rag', last_document_context=None)
        save_trace(response.run_id, response.trace)
        return response
    from app.workflow.planner import plan_request
    if public_ids is not None and plan_request(question) is None and not re.search(r'(?:查询|统计|计算|找出).*(?:销售额|数量|增长率|利润|净收入)', question):
        prev_ids = set((context.get('candidate_source_ids') or []) + (context.get('knowledge_document_ids') or []))
        # 只有范围真实来自前轮检索/核验的续问才算 followup，允许继承主题聚焦；
        # 前端显式指定新文件集合时 followup=False，不拼接旧主题。
        inherited_scope = bool(prev_ids) and bool(set(public_ids) & prev_ids)
        decision, detail = ConversationRoute(route='rag', source='knowledge',
            followup=inherited_scope), ('延续前轮来源范围的摘要续问' if inherited_scope else '明确公共文档集合的整体总结请求')
    else:
        with scoped_routing_sources(database_id, knowledge_base_id):
            decision, detail = await classify_conversation(question, state, attachment_ids or [])
    if public_ids is not None and knowledge_document_ids is None and decision.route == 'hybrid' and plan_request(question) is not None:
        public_ids = None
    if public_ids is not None and decision.route != 'rag':
        response = AgentResponse(run_id=str(uuid4()), task_type=decision.route, status='needs_clarification',
            message='本次选中了公共文档；如需同时查询数据库，请分别明确问数条件和文档用途。',
            trace=[_event('核对资料范围', started, '公共文档与问数任务范围不一致', 'needs_input')])
        update_session(session_id, last_result=response.model_dump(), last_document_context=None)
        save_trace(response.run_id, response.trace)
        return response
    failure = decision._failure
    route_event = _event("任务分类", started, f"{decision.route}；{detail}", "failed" if failure else "success")
    if failure:
        route_event["error"] = failure
        route_event["detail"] += "；" + failure_message(failure)
        if decision.route not in {"chat", "unknown"}:
            route_event["status"] = "degraded"
    if style_clauses:
        route_event['detail'] += '；已分离明确的表达要求：' + '、'.join(style_clauses)
    if decision.route in {"chat", "unknown"}:
        trace = [route_event]
        if cancelled:
            trace.append(_event("更新会话条件", time.perf_counter(), "已取消待处理业务任务，清除旧查询和文档条件"))
        response = AgentResponse(
            run_id=str(uuid4()), task_type=decision.route, status="needs_clarification",
            message="请说明您想进行普通交流、查询业务数据，还是依据文档回答；涉及业务事实时请补充查询对象或资料。",
            trace=trace,
        )
        if failure or "不可用" in detail:
            response.status = "failed"
            response.message = failure_message(failure or {"kind":"model_error"})
        elif decision.route == "unknown" and state.get("cancelled_task"):
            response.message = "之前的查询已取消。请重新说明要查询的指标、对象和时间，或继续普通交流。"
        elif decision.route == "chat":
            started = time.perf_counter()
            try:
                response.message = await answer_conversation(question, state)
                response.status = "completed"
                response.answer_source = "模型基础对话，未查询数据库或检索文档"
                trace.append(_event("基础对话生成", started, f"模型 {get_settings().chat_model} 返回文本；未调用业务数据工具"))
            except BailianChatIncomplete as exc:
                response.status = "partial_answer"
                response.message = exc.content + "\n\n本次回答达到输出长度上限，内容尚未完整结束。可回复“继续”接着说明。"
                response.answer_source = "模型基础对话，输出未完整结束；未查询数据库或检索文档"
                trace.append(_event("基础对话生成", started, "模型返回 length 结束标记，保留部分内容并提示续答", "partial_answer"))
            except BailianChatError as exc:
                response.status = "failed"
                response.message = failure_message(exc.diagnostic(), "基础对话生成")
                event = _event("基础对话生成", started, response.message, "failed")
                event["error"] = exc.diagnostic()
                trace.append(event)
        # Keep unfinished business conditions so explicit resumption is possible.
        response.trace = trace
        update_session(session_id, last_route=decision.route, last_result=response.model_dump())
        save_trace(response.run_id, trace)
        return response

    if public_ids is not None:
        attachment_ids = []
    elif decision.route in {"rag", "hybrid"}:
        effective_ids, scope_error = document_scope(question, state, decision, attachment_ids or [])
        if scope_error:
            response = AgentResponse(run_id=str(uuid4()), task_type=decision.route, status="needs_clarification",
                                     message=scope_error, trace=[route_event, _event("核对资料范围", time.perf_counter(), scope_error, "needs_input")])
            update_session(session_id, last_route=decision.route, last_result=response.model_dump())
            save_trace(response.run_id, response.trace)
            return response
        attachment_ids = effective_ids
    if not decision.followup and "不可用" not in detail:
        update_session(session_id, pending_message=None, pending_plan=None, pending_plan_attachment_ids=[], pending_evidence=None,
                       last_task=None, last_document_context=None)
    update_session(session_id, cancelled_task=False)
    reply_history = prepare_history(question, state, decision.route, attachment_ids or [])
    reply_history['reply_contract'] = contract
    from app.workflow.planner import plan_request
    resuming_plan = bool(state.get("pending_plan") and re.fullmatch(r"(?:19|20|21)\d{2}年?", question))
    if decision.route == "hybrid" and attachment_ids and plan_request(question) is None and not resuming_plan:
        from app.workflow.attachment_query import query_attachment
        from app.sources import SourceError, scoped_selection
        try:
            with scoped_selection(database_id, knowledge_base_id, route="hybrid",
                                  session_scoped=True, enforce=enforce_sources):
                response = await query_attachment(session_id, question, state, attachment_ids)
        except SourceError as exc:
            response = _source_denied_response(started, exc)
            update_session(session_id, last_route=decision.route, last_result=response.model_dump())
            save_trace(response.run_id, response.trace)
            return response
        response.trace.insert(0, route_event)
    else:
        execution_question = question
        context = state.get("last_document_context") or {}
        if (decision.route == "rag" and (decision.followup or re.search(r"该指标|这个指标|这个数字|该数字", question))
                and re.search(r"它|该指标|这个指标|这个数字|该数字", question)):
            subjects = set(re.findall(r"销售额|发票数量|客户数量|净收入|退款金额", context.get("question", "")))
            if len(subjects) == 1:
                execution_question = re.sub(r"它|该指标|这个指标|这个数字|该数字", next(iter(subjects)), question)
            elif len(subjects) > 1 or (not attachment_ids and re.search(r"该指标|这个指标|这个数字|该数字", question)):
                response = AgentResponse(run_id=str(uuid4()), task_type="rag", status="needs_clarification",
                    message="请明确“这个数字/该指标”指的是哪个指标，避免引用错误口径。",
                    trace=[route_event, _event("核对指代", time.perf_counter(), "上下文不能唯一确定指标", "needs_input")])
                update_session(session_id, last_route="rag", last_result=response.model_dump())
                save_trace(response.run_id, response.trace)
                return response
        if decision.route == "rag" and not attachment_ids:
            execution_question = public_retrieval_question(execution_question)
        from app.workflow.document_routing import document_followup, summary_followup
        if (decision.route == "rag" and decision.followup and not state.get("pending_evidence")
                and (document_followup(question, state) or summary_followup(question))
                and execution_question == public_retrieval_question(question)):
            topic = context.get("topic_question") or context.get("question")
            if topic:
                execution_question = f"此前文档主题：{topic}\n当前问题：{execution_question}"
        from app.workflow.document_routing import comparison_context
        comparison = comparison_context(question, state) if decision.route == 'rag' else ''
        if comparison:
            from app.workflow.document_routing import verify_term_comparison
            verified_comparison = verify_term_comparison(question, state)
            if verified_comparison is not None:
                verified_comparison.trace.insert(0, route_event)
                update_session(session_id, last_route='rag', last_result=verified_comparison.model_dump())
                save_trace(verified_comparison.run_id, verified_comparison.trace)
                return verified_comparison

        if comparison:
            execution_question += '\n' + comparison
        from app.sources import SourceError, scoped_selection
        try:
            if public_ids is not None:
                with scoped_selection(None, knowledge_base_id,
                                      route="rag", enforce=enforce_sources):
                    public_trace = [_event('选择处理路径', time.perf_counter(), 'rag'),
                                    _event('核对资料范围', time.perf_counter(),
                                          '使用明确的公共文档ID集合：' + ', '.join(public_ids))]
                    answer = await _answer_with_evidence(execution_question, public_trace,
                        knowledge_document_ids=public_ids, reply_preferences=contract)
                response = AgentResponse(run_id=str(uuid4()), task_type='rag', status=answer.status,
                    message=answer.message, evidence=_document_evidence(answer), claims=answer.claims,
                    evidence_assessment=answer.assessment, trace=public_trace)
            else:
                data_kwargs = {}
                if enforce_sources:
                    data_kwargs['database_id'] = database_id
                    data_kwargs['knowledge_base_id'] = knowledge_base_id
                    data_kwargs['enforce_sources'] = True
                response = await _run_data_agent(
                    session_id, execution_question, attachment_ids, decision.route, **data_kwargs
                )
        except SourceError as exc:
            response = _source_denied_response(started, exc)
            update_session(session_id, last_route=decision.route, last_result=response.model_dump())
            save_trace(response.run_id, response.trace)
            return response
        if execution_question != question:
            response.trace.insert(0, _event("规范化检索问题", time.perf_counter(), f"原问题：{question}；检索问题：{execution_question}"))
        response.trace.insert(0, route_event)
        response.task_type = decision.route
        if decision.route == "rag":
            retrieval_scope = (response.evidence_assessment or {}).get('retrieval_scope') or {}
            update_session(session_id, last_document_context={"question": execution_question,
                "topic_question": (context.get("topic_question") or context.get("question")) if decision.followup else execution_question,
                "attachment_ids": attachment_ids or [], "knowledge_document_ids": public_ids,
                "candidate_source_ids": retrieval_scope.get('candidate_source_ids', []),
                "candidate_displayed_source_ids": retrieval_scope.get('candidate_displayed_source_ids', []),
                "verified_chunk_ids": retrieval_scope.get('verified_chunk_ids', []),
                "kb_id": retrieval_scope.get('kb_id')})
    response.trace.insert(1, _event("读取历史对话", time.perf_counter(), f"已读取{len(reply_history['pairs'])}轮历史；事实仍由本次工具执行提供"))
    if original_question != question:
        step = '分离问题与表达要求' if style_clauses else '恢复复查问题'
        response.trace.insert(2, _event(step, time.perf_counter(), f"用户请求：{original_question}；本次执行问题：{question}"))
    reply_history = analyze_repeat(question, response, reply_history)
    response = await present_answer(question, response, history_context=reply_history)
    response = decorate_answer(response, reply_history)
    update_session(session_id, last_route=decision.route, last_result=response.model_dump())
    save_trace(response.run_id, response.trace)
    return response


async def _run_data_agent(
    session_id: str,
    message: str,
    attachment_ids: list[str] | None = None,
    route_hint: str | None = None,
    *,
    database_id: str | None = None,
    knowledge_base_id: str | None = None,
    enforce_sources: bool = False,
) -> AgentResponse:
    run_id, trace = str(uuid4()), []
    state = get_session(session_id)
    attachment_ids = attachment_ids or []
    question = message.strip()
    pending_plan = state.get("pending_plan")
    from app.workflow.plan_executor import execute_plan
    from app.workflow.planner import PlannedRequest, plan_request
    if pending_plan and re.fullmatch(r"(?:19|20|21)\d{2}年?", question):
        question = re.sub(r"^(根据退款规则计算|根据优惠资格规则找出|比较|对比|查询|找出|统计)",
                          lambda match: match[0] + question + " ", pending_plan, count=1)
        attachment_ids = attachment_ids or state.get("pending_plan_attachment_ids", [])
        trace.append(_event("读取会话条件", time.perf_counter(), "沿用跨源任务及会话附件，补充查询年度"))
    selected_documents = [
        document
        for document in state.get("documents", [])
        if document["id"] in attachment_ids
    ]
    document_chunks = [
        chunk for document in selected_documents for chunk in document["chunks"]
    ]
    from app.workflow.formula_inputs import formula_input_request, extract_formula_inputs
    if attachment_ids and formula_input_request(question):
        response = await extract_formula_inputs(session_id, question, selected_documents)
        # 视觉转写落在选中文档的页面上，需随会话持久化，供 /formula-page 原页接口返回。
        from app.storage.sessions import get_session as _reload, update_session as _save
        current = _reload(session_id)
        updated = list(current.get('documents', []))
        transcriptions = {d['id']: d for d in selected_documents}
        changed = False
        for document in updated:
            persisted = transcriptions.get(document['id'])
            if not persisted:
                continue
            for page in document.get('pages', []):
                visual = next((p.get('visual_transcription') for p in persisted.get('pages', [])
                               if p.get('page_no') == page.get('page_no')), None)
                if visual is not None and page.get('visual_transcription') != visual:
                    page['visual_transcription'] = visual
                    changed = True
        if changed:
            _save(session_id, documents=updated)
        update_session(session_id, last_result=response.model_dump(), pending_message=None, pending_evidence=None)
        save_trace(response.run_id, response.trace)
        return response
    if attachment_ids and plan_request(question) is None:
        trace.append(
            _event(
                "理解问题",
                time.perf_counter(),
                f"识别为会话附件问答，使用{len(selected_documents)}份附件",
            )
        )
        answer = await _answer_with_evidence(
            question,
            trace,
            extra_chunks=document_chunks,
            reply_preferences=state.get('active_reply_contract'),
        )
        response = AgentResponse(
            run_id=run_id,
            status=answer.status,
            message=answer.message,
            evidence=_document_evidence(
                answer,
                session_id=session_id,
                session_scoped=True,
            ),
            trace=trace,
            claims=answer.claims,
            evidence_assessment=answer.assessment,
            clarification_options=evidence_options(answer),
        )
        update_session(
            session_id,
            pending_message=None,
            pending_evidence=None,
            last_result=response.model_dump(),
        )
        save_trace(run_id, response.trace)
        return response

    planned = plan_request(question)
    if planned is not None:
        trace.append(
            _event(
                "理解问题",
                time.perf_counter(),
                f"识别为 hybrid，计划类型 {planned.kind}；{planned.task.mode_reason}",
            )
        )
        if planned.task.unsupported_reason:
            response = AgentResponse(
                run_id=run_id,
                status="unsupported",
                message=planned.task.unsupported_reason,
                trace=trace,
            )
        elif planned.task.needs_clarification:
            prompt, options = build_clarification(planned.task)
            update_session(session_id, pending_plan=question, pending_message=None, pending_plan_attachment_ids=attachment_ids)
            trace.append(
                _event(
                    "检查关键条件",
                    time.perf_counter(),
                    "跨源任务条件缺失，未执行子任务",
                    "needs_input",
                )
            )
            save_trace(run_id, trace)
            return AgentResponse(
                run_id=run_id,
                status="needs_clarification",
                message=prompt,
                clarification_options=options,
                trace=trace,
                query_conditions=describe_task(planned.task),
            )
        else:
            try:
                with scoped_selection(database_id, knowledge_base_id, route="hybrid",
                                      session_scoped=bool(attachment_ids), enforce=enforce_sources):
                    response = await execute_plan(planned, run_id, trace,
                                                  extra_chunks=document_chunks if attachment_ids else None, session_id=session_id)
            except SourceError as exc:
                response = _source_denied_response(time.perf_counter(), exc)
                save_trace(run_id, response.trace)
                return response
        update_session(
            session_id,
            pending_plan=None,
            pending_plan_attachment_ids=[],
            pending_message=None,
            pending_evidence=None,
            last_task=planned.task.model_dump(),
            last_result=response.model_dump(),
        )
        save_trace(run_id, response.trace)
        return response

    update_session(session_id, pending_plan=None, pending_plan_attachment_ids=[])
    question, evidence_version, evidence_followup = resolve_evidence_followup(
        question,
        state.get("pending_evidence"),
    )
    if evidence_followup:
        trace.append(
            _event(
                "读取会话证据条件",
                time.perf_counter(),
                f"沿用待补充的文档问题；本轮明确补充：{message.strip()}",
            )
        )
    elif state.get("pending_evidence"):
        update_session(session_id, pending_evidence=None)
    from app.workflow.query_clarification import resolve_quantity_followup
    quantity_resolution = resolve_quantity_followup(question, state) if not attachment_ids and route_hint != 'rag' else None
    if quantity_resolution is not None:
        trace.append(_event('读取待澄清问数条件', time.perf_counter(), json.dumps({
            '原问题': quantity_resolution.context['origin'], '本轮补充': question,
            '明确年份': quantity_resolution.context['years'], '明确国家': quantity_resolution.context['countries'],
            '统计对象已补充': quantity_resolution.context['measure_confirmed'],
            '实际解析输入': quantity_resolution.question,
        }, ensure_ascii=False)))
        question = quantity_resolution.question
        state = {**state, '_quantity_resolution': quantity_resolution}
    elif state.get("pending_message"):
        if ((state.get('last_task') or {}).get('parsing_error')
                and re.fullmatch(r'(?:请)?(?:重试|再试一次|重新执行|再执行一次)[。！!\s]*', question)):
            question = state['pending_message']
        else:
            question = f"{state['pending_message']}\n用户补充：{question}"

    started = time.perf_counter()
    task = (QueryTask(intent="rag", raw_question=question, parsing_mode="model_checked",
                      mode_reason="文档路由：检索原文并校验证据")
            if route_hint == "rag" else await parse_intent(question, state))
    if quantity_resolution is not None and task.needs_clarification:
        task.clarification_context = quantity_resolution.context
    if route_hint in {"sql", "hybrid"} and task.intent != route_hint and not task.unsupported_reason:
        task.needs_clarification = True
        task.ambiguities.append("任务分类与业务解析的数据来源不一致，请明确需要数据库、文档还是两者结合；暂未执行。")
    trace.append(
        _event(
            "理解问题",
            started,
            f"识别为 {task.intent}，指标 {task.metric_id or '无'}；{task.mode_reason}",
        )
    )
    if task.element_evidence:
        trace.append(_event("核对问题要素", time.perf_counter(), json.dumps({"原问题":task.raw_question,
            "规范化输入":task.normalized_question,"标准问题":task.canonical_question, "来源":task.source_id,
            "识别要素":task.element_evidence, "待解析":task.unresolved_fragments}, ensure_ascii=False)))
    if task.parsing_error:
        trace.append(_event('核对解析输出', time.perf_counter(), task.mode_reason, 'failed'))
        # Retain the full request including the latest supplement. A retry must
        # not lose conditions that the user has already clarified.
        update_session(session_id, pending_message=question,
                       last_task=task.model_dump())
        save_trace(run_id, trace)
        return AgentResponse(run_id=run_id, task_type=task.intent, status='failed',
            message='系统未能完成查询解析，未执行 SQL。请重试本次查询；原问题及已确认条件已保留。',
            trace=trace)
    conditions = describe_task(task) if task.intent in {"sql", "hybrid"} else None
    if task.unsupported_reason:
        trace.append(_event("能力检查", time.perf_counter(), task.unsupported_reason, "unsupported"))
        update_session(session_id, pending_message=None)
        response = AgentResponse(
            run_id=run_id,
            status="unsupported",
            message=task.unsupported_reason,
            query_conditions=conditions,
            trace=trace,
        )
        save_trace(run_id, trace)
        return response
    if task.needs_clarification:
        prompt, options = build_clarification(task)
        update_session(session_id, pending_message=question, last_task=task.model_dump())
        trace.append(
            _event(
                "检查关键条件",
                time.perf_counter(),
                "发现缺失或歧义条件",
                "needs_input",
            )
        )
        save_trace(run_id, trace)
        return AgentResponse(
            run_id=run_id,
            status="needs_clarification",
            message=prompt,
            trace=trace,
            clarification_options=options,
            query_conditions=conditions,
        )

    update_session(session_id, pending_message=None, last_task=task.model_dump())
    route = route_task(task)
    trace.append(_event("选择处理路径", time.perf_counter(), route))
    # The authoritative source gate is keyed to the actual task route (from
    # parse_intent / planner), not the conversational pre-classification: a
    # request classified as "sql" may still carry a hybrid entity_advice plan
    # that retrieves documents, so hybrid needs both a database and a knowledge
    # base no matter what the early router guessed.
    source_route = route
    try:
        if route == "hybrid":
            with scoped_selection(database_id, knowledge_base_id, route="hybrid",
                                  session_scoped=bool(attachment_ids), enforce=enforce_sources):
                response = await execute_plan(
                    PlannedRequest("entity_advice", task, question),
                    run_id,
                    trace,
                )
            update_session(session_id, last_result=response.model_dump())
            save_trace(run_id, response.trace)
            return response

        with scoped_selection(database_id, knowledge_base_id, route=source_route,
                              session_scoped=bool(attachment_ids), enforce=enforce_sources):
            return await _execute_single_route(
                session_id, question, attachment_ids, route, evidence_version,
                evidence_followup, state, trace, message, task, run_id, conditions,
            )
    except SourceError as exc:
        response = _source_denied_response(time.perf_counter(), exc)
        update_session(session_id, last_route=route, last_result=response.model_dump())
        save_trace(run_id, response.trace)
        return response


async def _execute_single_route(session_id, question, attachment_ids, route,
                                evidence_version, evidence_followup, state, trace, message,
                                task, run_id, conditions):
    """Execute the single-route (rag or sql) branch inside the active source scope."""
    try:
        if route == "rag":
            retrieval_question = normalize_question(question)
            if retrieval_question != question:
                trace.append(
                    _event(
                        "规范化检索表达",
                        time.perf_counter(),
                        f"原问题：{question}；检索表达：{retrieval_question}",
                    )
                )
            answer = await _answer_with_evidence(
                retrieval_question,
                trace,
                version=evidence_version,
                reply_preferences=state.get('active_reply_contract'),
            )
            response = AgentResponse(
                run_id=str(uuid4()),
                status=answer.status,
                message=answer.message,
                evidence=_document_evidence(answer),
                trace=trace,
                claims=answer.claims,
                evidence_assessment=answer.assessment,
                clarification_options=evidence_options(answer),
            )
            versions = {
                chunk.get("source_metadata", {}).get("version")
                for chunk in answer.chunks
                if chunk.get("source_metadata", {}).get("version")
            }
            if not versions and evidence_followup:
                versions = set((state.get("pending_evidence") or {}).get("versions", []))
            update_session(
                session_id,
                pending_evidence=(
                    {
                        "question": question,
                        "version": evidence_version,
                        "versions": sorted(versions),
                    }
                    if answer.status
                    in {
                        "conflicting_evidence",
                        "insufficient_evidence",
                        "partial_answer",
                        "knowledge_unavailable",
                    }
                    else None
                ),
            )
        else:
            started = time.perf_counter()
            generated = await generate_sql(task)
            validate_query_contract(generated.sql, generated.params, task)
            trace.append(_event("生成并校验 SQL", started, generated.explanation))
            trace.append(
                _event(
                    "查询条件核对",
                    time.perf_counter(),
                    "指标、国家、时间、分组、排名和比较周期与执行 SQL 一致",
                )
            )
            started = time.perf_counter()
            sql_result = await asyncio.to_thread(execute_sql, generated.sql, generated.params)
            trace.append(_event("只读执行 SQL", started, f"返回 {sql_result['row_count']} 行"))
            sql_result, result_status = check_result(task, sql_result)
            trace.append(
                _event(
                    "结果校验",
                    time.perf_counter(),
                    f"有效结果 {sql_result['valid_row_count']} 行；已检查空数据、基期和计算结果",
                    result_status,
                )
            )
            sql_evidence = Evidence(
                evidence_id=f"sql-{run_id}",
                source_type="database",
                title="数据库查询结果",
                content=str(sql_result["rows"]),
                metadata={"sql": generated.sql, "params": generated.params},
            )
            response = AgentResponse(
                run_id=run_id,
                status=result_status,
                message=render_sql_answer(task, sql_result),
                data=sql_result,
                sql=generated.sql,
                sql_params=generated.params,
                evidence=[sql_evidence],
                trace=trace,
            )
    except UnsupportedQuery as exc:
        trace.append(_event("能力检查", time.perf_counter(), str(exc), "unsupported"))
        response = AgentResponse(
            run_id=run_id,
            status="unsupported",
            message=str(exc),
            trace=trace,
        )
    except Exception as exc:
        trace.append(_event("执行失败", time.perf_counter(), str(exc), "failed"))
        response = AgentResponse(
            run_id=run_id,
            status="failed",
            message=f"任务执行失败：{exc}",
            trace=trace,
        )
    response.query_conditions = conditions
    update_session(session_id, last_result=response.model_dump())
    save_trace(run_id, response.trace)
    return response
