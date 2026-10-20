"""Source intent and bounded, read-only corpus context for task classification."""
import json
import re
from functools import lru_cache

from app.config import get_settings


def standalone_chat(question: str) -> bool:
    # Full matches only: greetings followed by a factual task must retain that task.
    return bool(re.fullmatch(
        r"(?:你好|您好|嗨|哈喽|谢谢(?:你|您)?|多谢|谢谢你的帮助|辛苦了|再见|"
        r"你是谁|你叫什么(?:名字)?|你能做什么|hello|hi|thanks|thank you|"
        r"(?:什么是|解释一下|介绍一下)(?:知识库|数据库|RAG|大模型)|"
        r"(?:知识库|数据库|RAG|大模型)(?:是什么|是什么意思))"
        r"[！!。.?？\s]*", question.strip(), re.I))


def explicit_document_request(question: str) -> bool:
    return bool(re.search(
        r"(?:根据|依据|查阅|检索|查询|从|查一下|查找).*(?:知识库|文档|资料|附件|文件|报告|文章|手册)|"
        r"(?:知识库|文档|资料|附件|文件)(?:中|里|内|上|的|提到|说明|规定|写)|"
        r"这(?:份|篇|个)(?:文档|资料|附件|文件|报告|文章)|"
        r"(?:总结|概括|翻译|解读|阅读).*(?:文档|附件|文件|报告|文章)|"
        r"\b(?:according to|based on|in the)\b.*\b(?:document|report|file|manual|article|knowledge base)\b",
        question, re.I))


def document_followup(question: str, state: dict) -> bool:
    context = state.get('last_document_context') or {}
    if not context.get('question'):
        return False
    explicit_resume = bool(re.search(r"继续.*(?:刚才|之前|前面).*(?:文档|附件|资料)|刚才(?:的)?(?:文档|附件|资料)", question))
    active = state.get('last_route') == 'rag'
    return bool((active or explicit_resume) and re.search(
        r"^(?:这|那|上述|前面)(?:两|二|几|些|个).*(?:一致|相同|一样|区别|不同|同一)|"
        r"^(?:那|那么)?(?:它|该文|这篇|这份)|"
        r"^(?:那|那么)?(?:具体要求|适用范围|有效期|负责人|实施日期|申请条件|还有哪些要求|为什么这样规定|有什么限制|有什么例外)[呢吗？?。\s]*$|"
        r"(?:文中|上文|该文件|该报告|上述要求)|"
        r"^(?:does it |what does it |what about its |and its )",
        question, re.I))


def summary_retry(question: str) -> bool:
    """Only an explicit generic retry; a new named topic must keep its own scope."""
    return bool(re.fullmatch(
        r'(?:请|帮我|请帮我)?(?:重新|再次|再|继续)(?:提取|总结|概括|提炼|归纳|整理|生成)'
        r'(?:一下|一次)?(?:主要内容|核心内容|内容|摘要|总结|文档摘要|主要内容摘要)?[！!。？?\s]*',
        question.strip()))


def summary_followup(question: str) -> bool:
    """“总结相关文档”“总结这些新闻”“把刚才检索到的内容概括一下”“把刚才检索到的新闻总结一下”
    等对前轮主题与有效来源的续问。仅当表述明确回指前轮检索/总结结果时为 True；
    全新的主题检索（“总结特朗普在2020年的做法”）不命中。
    """
    if summary_retry(question):
        return True
    if not re.search(r'总结|概括|提炼|摘要|归纳|整理', question):
        return False
    return bool(re.search(
        r'相关文档|这些新闻|这些报道|这些资料|'
        r'刚才(?:检索|查到|找到|问).*|刚才.*(?:内容|新闻|报道|文档|资料|结果)|'
        r'把刚才.*(?:概括|总结)|把.*刚才.*(?:概括|总结)|'
        r'再(?:总结|概括|提炼)|'
        r'(?:总结|概括|提炼).{0,16}(?:新闻|报道).{0,16}(?:摘要|概括|总结)|'
        r'新闻.*提取成摘要',
        question, re.I))


@lru_cache(maxsize=4)
def _index(path: str, modified: int, size: int) -> dict:
    from app.rag.indexer import load_persisted_index
    return load_persisted_index(path,modified,size)


def _persisted_index() -> dict:
    path = get_settings().indexes_path / 'knowledge_index.json'
    try:
        stat = path.stat()
        return _index(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def knowledge_catalog() -> list[dict]:
    data = _persisted_index()
    # Previews are classification data, never evidence or instructions.
    return [{'title': str(source.get('title', ''))[:180],
             'filename': str(source.get('filename', ''))[:180],
             'preview': ' '.join(str(page.get('text', '')) for page in source.get('pages', [])[:2])[:600]}
            for source in list(data.get('sources', {}).values())[:24]]


@lru_cache(maxsize=4)
def _routing_lexical(path: str, modified: int, size: int) -> dict:
    from app.rag.lexical import build_lexical_index
    data = _index(path, modified, size)
    # The legacy normalizer removes spaces, joining English words together.
    # Boundaries in this classification-only index preserve individual words;
    # the stored documents, RAG index and frozen evaluations are unchanged.
    return build_lexical_index([{**c, **{key: re.sub(r'\s+', ' . ', c.get(key, ''))
        for key in ('text', 'section', 'title')}} for c in data.get('chunks', [])])


def routing_candidates(question: str) -> list[dict]:
    """Real lexical candidates for classification; final RAG must recheck evidence."""
    from app.rag.lexical import bm25_scores
    from app.rag.language import canonicalize
    data = _persisted_index()
    try:
        path = get_settings().indexes_path / 'knowledge_index.json'
        stat = path.stat()
        lexical = _routing_lexical(str(path), stat.st_mtime_ns, stat.st_size)
        scores = bm25_scores([re.sub(r'\s+', ' . ', canonicalize(question))], lexical)
        candidates = sorted((c for c in data.get('chunks', []) if scores.get(c['chunk_id'], 0) > 0),
                            key=lambda c: scores[c['chunk_id']], reverse=True)[:3]
        return [{'title': c.get('title', ''), 'section': c.get('section', ''),
                 'preview': c.get('text', '')[:1000], 'score': round(scores[c['chunk_id']], 3)} for c in candidates]
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        return []


def comparison_context(question: str, state: dict) -> str:
    """Resolve comparison referents; history is a search hint, never evidence."""
    if not (document_followup(question, state) and re.search(r'一致|相同|一样|区别|不同|同一', question)):
        return ''
    turns = []
    for message in reversed(state.get('messages', [])):
        result = message.get('result') or {}
        if (message.get('role') == 'assistant' and result.get('task_type') == 'rag'
                and result.get('status') in {'completed', 'partial_answer'} and result.get('evidence')):
            text = message.get('text', result.get('message', ''))
            # An intervening answer about an unrelated object is not a referent.
            if re.search(r'罪|crime|charge', question, re.I) and not re.search(
                    r'罪|crime|charge|felony|misdemeanor|manslaughter', text, re.I):
                continue
            if any(event.get('step') == '规范化检索问题' and
                   '比较的是前两轮译名所对应的原文术语' in event.get('detail', '')
                   for event in result.get('trace', [])):
                continue
            turns.append(text[:1200])
            if len(turns) == 2:
                break
    if len(turns) != 2:
        return ''
    ordered = list(reversed(turns))
    terms = [re.findall(r'[（(]([A-Za-z][A-Za-z -]{3,80})[）)]', text) for text in ordered]
    if all(terms):
        return ('比较的是前两轮译名所对应的原文术语，不是不同主体的法律责任；'
                '请重新检索来源判断术语是否相同，并指出前文译名或主体表述错误。\n'
                + '\n'.join(f'比较对象{i + 1}原文术语：' + ', '.join(names) for i, names in enumerate(terms)))
    return '此前两轮回答仅用于识别比较对象，必须重新检索原文核验：\n' + '\n'.join(ordered)


def terminology_context(state: dict) -> dict:
    terms = {}
    for message in state.get('messages', [])[-12:]:
        if message.get('role') != 'assistant' or not (message.get('result') or {}).get('evidence'):
            continue
        text = message.get('text', '')
        text = re.sub(r'(?:犯有|被控)([\u4e00-\u9fff]{2,16}罪)(?=[（(])', r'“\1”', text)
        for match in re.finditer(r'[“\"]([^”\"\n]{2,35})[”\"]\s*[（(]([A-Za-z][A-Za-z -]{3,80})[）)]', text):
            terms.setdefault(match.group(2), match.group(1))
    return terms


def verify_term_comparison(question, state):
    """Compare source identifiers, not legal equivalence or generated translations."""
    if not document_followup(question, state) or not re.search(r'罪.*(?:一致|相同|一样|区别|不同|同一)', question):
        return None
    refs=[]
    corpus=_persisted_index().get('chunks', [])
    for m in reversed(state.get('messages', [])):
        result=m.get('result') or {}
        if result.get('task_type')!='rag' or result.get('status') not in {'completed','partial_answer'}:
            continue
        if any(e.get('step')=='核对原文术语' for e in result.get('trace', [])):
            continue
        text=m.get('text',result.get('message',''))
        if not re.search(r'罪|manslaughter|felony|misdemeanor',text,re.I):
            continue
        terms=re.findall(r'[（(]([A-Za-z][A-Za-z -]{3,80})[）)]',text)
        if not terms:
            continue
        term=terms[0]
        for evidence in result.get('evidence', []):
            content=evidence.get('content','')
            if term.casefold() not in content.casefold():
                continue
            if any(c.get('chunk_id')==evidence.get('evidence_id') and c.get('text')==content for c in corpus):
                refs.append((term,evidence));break
        if len(refs)==2:break
    if len(refs)!=2:return None
    refs.reverse()
    from uuid import uuid4
    from app.schemas.response import AgentResponse
    from app.schemas.evidence import Evidence
    evidence=[]
    for _,item in refs:
        if not any(e.evidence_id==item['evidence_id'] for e in evidence):
            copy={**item,'metadata':{**item.get('metadata',{}),'citation':len(evidence)+1}}
            evidence.append(Evidence.model_validate(copy))
    a,b=refs[0][0],refs[1][0]
    citations=''.join(f'[{i+1}]' for i in range(len(evidence)))
    if a.casefold()==b.casefold():
        answer=f'前两条相关回答引用的英文名称均为 {a}，指向同一个原文术语。此前不同的中文名称是译法差异，不应据此判断为不同罪名。中文译名仅供理解，并非资料提供的正式中文名称。{citations}'
    else:
        answer=f'前两条相关回答引用的英文名称分别为 {a} 和 {b}，原文名称不同。不能仅凭中文译名判断它们相同；具体构成要件或法律适用是否一致，仍需分别核对完整条文。{citations}'
    return AgentResponse(run_id=str(uuid4()),task_type='rag',status='partial_answer',message=answer,evidence=evidence,
        trace=[{'step':'核对原文术语','status':'success','detail':'重新读取当前索引，核对历史引用ID、原文内容和英文术语；仅比较名称，不推断法律适用','duration_ms':0}])
