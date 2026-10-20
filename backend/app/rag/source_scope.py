"""Resolve explicit public summary scopes from real source metadata, never hits."""
import re

from app.rag.indexer import read_serving_index as get_local_index
from app.rag.summary_intent import is_document_summary, news_topic
from app.workflow.document_routing import document_followup, summary_followup, summary_retry, standalone_chat, explicit_document_request


def retry_document_ids(state, question):
    """Recover the previous whole-document scope, including legacy failed retries.

    Stop at a substantive user request; never search across a topic change.
    A lexical candidate set from a misrouted generic retry is not a user selection.
    """
    if state.get('last_route') != 'rag' or not summary_retry(question):
        return None
    context = state.get('last_document_context') or {}
    if context.get('knowledge_document_ids'):
        return context['knowledge_document_ids']
    for message in reversed(state.get('messages', [])):
        if message.get('role') == 'user':
            if summary_retry(message.get('text', '')):
                continue
            return message.get('knowledge_document_ids') or None
        result = message.get('result') or {}
        assessment = result.get('evidence_assessment') or {}
        scope = assessment.get('retrieval_scope') or {}
        if result.get('task_type') == 'rag' and scope.get('retrieval_mode') == 'document_browse':
            return scope.get('candidate_source_ids') or None
        if result.get('task_type') in {'sql', 'chat', 'hybrid'}:
            break
    return None


def _public_summary_scope(question, state, attachment_ids, requested_ids=None, knowledge_base_id=None):
    if not is_document_summary(question, document_context=True):
        if requested_ids is not None:
            return None, "当前公共文档选择用于整体总结，请明确需要总结的内容。"
        return None, None
    all_public = bool(re.search(
        r"(?:全部|所有).*?(?:知识库|公共文档)|(?:知识库|公共文档).*?(?:全部|所有)", question))
    context = state.get('last_document_context') or {}
    retry_ids = retry_document_ids(state, question) if requested_ids is None else None
    # 续问可继承的不仅是已核验文档集合（knowledge_document_ids），还包括核验
    # 失败/部分通过时仅保留的候选来源范围（candidate_source_ids）。前者语义是
    # 显示给用户的已采纳集合，后者是本次检索真正用到、可能被后续核验淘汰的
    # 完整候选集。两者都必须真实来自前轮检索，不能凭空生成。
    inherited = (state.get('last_route') == 'rag'
                 and (retry_ids or context.get('knowledge_document_ids') or context.get('candidate_source_ids'))
                 and (document_followup(question, state) or summary_followup(question)
                      or re.search(r'这些|它们|上述|刚才|再总结', question))
                 and not re.search(r'知识库|公共文档|附件|上传|换(?:一个|个|一下|成)', question))
    if attachment_ids and (requested_ids is not None or all_public):
        return None, "请分别选择公共知识库文档或当前对话附件，避免混淆来源范围。"
    if attachment_ids or re.search(r'附件|上传', question):
        return None, None
    if requested_ids is None and not all_public and any(d.get('name') and d['name'] in question for d in state.get('documents', [])):
        return None, None
    index = get_local_index()
    sources = index['sources']
    remaining = question
    for source in sources.values():
        remaining = remaining.replace(source['filename'], ' ')
    if re.search(r'[\w().-]+\.(?:ohr\.json|pdf|docx|txt|md)\b', remaining, re.I):
        return None, "问题提及了未找到的公共文件，请核对文件名或上传资料；本次未省略该文件。"
    named_files = [key for key, source in sources.items() if source['filename'] in question]
    if requested_ids is not None and named_files and not set(named_files).issubset(requested_ids):
        return None, "问题中的文件名与所选公共文档不一致，请确认需要总结的文件。"
    if requested_ids is not None and all_public and set(requested_ids) != set(sources):
        return None, "问题要求全部知识库文档，但本次只选择了部分文件，请确认范围。"
    ids = requested_ids
    if ids is None and all_public:
        ids = list(sources)
    if ids is None:
        named = named_files
        if not named:
            named = [key for key, source in sources.items()
                     if len(source['title']) >= 3 and source['title'] in question]
            titles = [sources[key]['title'] for key in named]
            if len(titles) != len(set(titles)):
                return None, "存在同名公共文档，请在知识库面板选择明确的文件。"
        if named:
            ids = named
        elif inherited:
            # 知识库已切换时不继承旧来源；需重新检索确认新范围。
            prev_kb = context.get('kb_id')
            if prev_kb and knowledge_base_id and prev_kb != knowledge_base_id:
                return None, "知识库已切换，前轮检索到的来源不适用于当前知识库；请重新检索或确认使用的知识库。"
            # 主题已切换时不继承旧主题的来源（具体澄清而非静默换范围）。
            new_topic = '' if summary_retry(question) else news_topic(question)
            prev_topic = news_topic(context.get('topic_question') or context.get('question') or '')
            if new_topic and prev_topic and new_topic != prev_topic:
                return None, (f"您的问题主题已从「{prev_topic}」切换为「{new_topic}」；"
                              f"如需总结新主题，请重新检索或明确选择文件。")
            ids = retry_ids or context.get('knowledge_document_ids') or context.get('candidate_source_ids')
    if ids is None:
        return None, None
    ids = list(dict.fromkeys(ids))
    if not ids:
        return None, "没有选中可用的公共文档，请刷新知识库并选择需要总结的文件。"
    if any(key not in sources for key in ids):
        return None, "所选公共文档已更新或不可用，请刷新知识库并重新选择；本次未替换成其他资料。"
    return ids, None


def public_document_scope(question, state, attachment_ids, requested_ids=None, knowledge_base_id=None):
    if is_document_summary(question, document_context=True):
        return _public_summary_scope(question, state, attachment_ids, requested_ids, knowledge_base_id)
    previous_kb = (state.get('last_document_context') or {}).get('kb_id')
    if previous_kb and knowledge_base_id and previous_kb != knowledge_base_id and document_followup(question, state):
        return None, '知识库已切换，请重新选择当前知识库的文档。'
    from app.config import get_settings
    if (get_settings().dataset_profile != 'public' and requested_ids is None
            and not is_document_summary(question, document_context=True)):
        # Keep the versioned business-rule/hybrid planner authoritative. A rule
        # title is not an instruction to bypass its data-query dependencies.
        return None, None
    from app.workflow.conversation import text_transformation
    if standalone_chat(question) or text_transformation(question):
        if requested_ids is not None:
            return None, "请提出所选文档的具体问题或总结请求；普通交流请先取消文档选择。"
        return None, None
    all_public = bool(re.search(
        r"(?:全部|所有).*?(?:知识库|公共文档)|(?:知识库|公共文档).*?(?:全部|所有)", question))
    context = state.get('last_document_context') or {}
    inherited = (state.get('last_route') == 'rag' and context.get('knowledge_document_ids')
                 and (document_followup(question, state) or re.search(r'这些|它们|上述|刚才|再总结', question))
                 and not re.search(r'知识库|公共文档|附件|上传', question))
    if attachment_ids and (requested_ids is not None or all_public):
        return None, "请分别选择公共知识库文档或当前对话附件，避免混淆来源范围。"
    if attachment_ids or re.search(r'附件|上传', question):
        return None, None
    if requested_ids is None and not all_public and any(d.get('name') and d['name'] in question for d in state.get('documents', [])):
        return None, None
    index = get_local_index()
    sources = index['sources']
    remaining = question
    for source in sources.values():
        remaining = remaining.replace(source['filename'], ' ')
    if re.search(r'[\w().-]+\.(?:ohr\.json|pdf|docx|txt|md)\b', remaining, re.I):
        return None, "问题提及了未找到的公共文件，请核对文件名或上传资料；本次未省略该文件。"
    named_files = [key for key, source in sources.items() if source['filename'] in question]
    named_titles = [key for key, source in sources.items()
                    if len(source['title']) >= 3 and source['title'] in question]
    if requested_ids is not None and named_files and not set(named_files).issubset(requested_ids):
        return None, "问题中的文件名与所选公共文档不一致，请确认需要查询的文件。"
    if requested_ids is not None and named_titles and not set(named_titles).intersection(requested_ids):
        return None, "问题中的资料标题与所选公共文档不一致，请确认需要查询的文件。"
    from app.rag.evidence_completion import named_source_scope
    named_source = named_source_scope(question, sources)
    if (requested_ids is not None and named_source
            and not set(named_source['doc_ids']).issubset(requested_ids)):
        return None, "问题明确的资料名称与所选公共文档不一致，请确认需要查询的文件。"
    if requested_ids is not None and all_public and set(requested_ids) != set(sources):
        return None, "问题要求全部知识库文档，但本次只选择了部分文件，请确认范围。"
    ids = requested_ids
    if ids is None and all_public:
        ids = list(sources)
    if ids is None:
        named = named_files
        if not named:
            named = named_titles
            titles = [sources[key]['title'] for key in named]
            if len(titles) != len(set(titles)):
                return None, "存在同名公共文档，请在知识库面板选择明确的文件。"
        if named:
            ids = named
        elif inherited:
            ids = context['knowledge_document_ids']
        else:
            if named_source:
                ids = named_source['doc_ids']
            else:
                from app.rag.source_aliases import mentioned_aliases
                if (re.search(r'写(?:一|封|段|篇)|创作|编写|翻译|润色', question)
                        and not explicit_document_request(question)):
                    return None, None
                links = mentioned_aliases(question)
                alias_ids = list(dict.fromkeys(link['doc_id'] for link in links))
                if len(alias_ids) > 1:
                    return None, "该缩写在多份公共文档中出现，请选择需要依据的一份文档或补充完整名称，避免混淆来源。"
                if alias_ids:
                    ids = alias_ids
    if ids is None:
        return None, None
    ids = list(dict.fromkeys(ids))
    if not ids:
        return None, "没有选中可用的公共文档，请刷新知识库并选择需要查询的文件。"
    if any(key not in sources for key in ids):
        return None, "所选公共文档已更新或不可用，请刷新知识库并重新选择；本次未替换成其他资料。"
    return ids, None



# Legacy imports support factual questions as well as whole-document summaries.
public_summary_scope = public_document_scope
