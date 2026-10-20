"""Question-level sufficiency; retrieval budgets are execution facts, not verdicts."""
import re


def requires_full_scope(question):
    return bool(re.search(r'全库|整个知识库|所有文档|全部文档|所有.*(?:列出|有哪些)|列出所有|完整列出|'
                          r'没有任何|不存在任何|\b(?:all documents|entire corpus|list all|no documents)\b',question,re.I))


def scope_is_incomplete(retrieval):
    budget=retrieval.diagnostics.get('retrieval_budget',{})
    applicable=budget.get('applicable')
    if applicable is None:
        applicable=len(retrieval.corpus_chunks) if retrieval.corpus_chunks is not None else None
    # No lexical match is not proof that a fact is absent from unread text.
    return (applicable is None or applicable>budget.get('selected',0)
            or budget.get('matched',0)>budget.get('selected',0)
            or bool(retrieval.warnings) or retrieval.knowledge_status!='ready')


def completion_assessment(question,verification,retrieval,cited):
    reasons=[]
    if verification.get('answer_complete') is not True:
        reasons.append('当前证据尚未确认问题的全部要素')
    if verification.get('context_complete') is not True:
        reasons.append('必要的前提或上下文尚未完整核对')
    if verification.get('conflicts_found') is not False:
        reasons.append('尚未确认相关证据一致')
    if any(c.get('ocr_untrusted') for c in cited):
        reasons.append('支持片段包含识别不可靠的区域')
    full=requires_full_scope(question) or verification.get('requires_full_scope') is True
    if full and scope_is_incomplete(retrieval):
        reasons.append('该问题需要完整范围检查，本次尚未覆盖全部适用证据')
    return {'sufficient':not reasons,'reasons':reasons,'requires_full_scope':full,
            'verification':verification}


def named_source_scope(question,sources):
    """Only a unique literal source subject in an explicit request can narrow scope."""
    match=re.search(r'(?:根据|依据)\s*([A-Za-z][A-Za-z0-9 .&_-]{3,100})\s*(?=的|介绍|说明|手册|报告|文档|资料|[（(])',question)
    if not match:return None
    normalize=lambda text:re.sub(r'[\W_]+','',text.casefold())
    subject=match[1].strip();needle=normalize(subject)
    if len(needle)<5:return None
    matching=[]
    for key,source in sources.items():
        pages=[p['text'] for p in source.get('pages',[]) if p.get('text','').strip()][:2]
        introduction=str(source.get('title',''))+' '+' '.join(pages)[:5000]
        if needle in normalize(introduction):matching.append(key)
    return {'doc_ids':matching,'subject':subject} if len(matching)==1 else None
