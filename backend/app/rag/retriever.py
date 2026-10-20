"""Union of lexical and real vector recall, always subject to metadata gates."""
import re
from time import perf_counter
import asyncio
from dataclasses import dataclass, field

from app.config import get_settings
from app.rag.indexer import read_serving_index as get_local_index
from app.rag.language import canonicalize
from app.rag.summary_intent import is_document_summary

from app.rag.lexical import bm25_scores, coverage_scores, build_lexical_index, normalize, terms

COUNTRIES = {"美国": "USA", "加拿大": "Canada", "中国": "China", "法国": "France", "德国": "Germany"}


def facets(query: str) -> list[str]:
    parts = [part for part in re.split(r"以及|并且|同时|和|及|、", query) if terms(part)] or [query]
    # In “销售额的口径和单位”, the bare attribute still belongs to 销售额.
    # Otherwise a second document's unrelated unit could falsely fill this aspect.
    subject = re.split(r"口径|定义|单位|公式|生效|期限|统计", canonicalize(parts[0]), maxsplit=1)[0].rstrip("的 ")
    bare_attributes = {"单位", "口径", "公式", "生效", "期限", "适用范围"}
    if subject and terms(subject):
        parts = [parts[0], *[f"{subject}的{part}" if normalize(part).strip("？?。！!") in bare_attributes else part
                            for part in parts[1:]]]
    return parts


def lexical_coverage(query: str, text: str) -> float:
    q = terms(query)
    return len(q & terms(text)) / len(q) if q else 0.0


@dataclass
class RetrievalResult:
    chunks: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    knowledge_status: str = "ready"
    mode: str = "lexical"
    candidate_count: int = 0
    query: str = ""
    normalized_query: str = ""
    rerank_status: str = "not_requested"
    answer_rejection: str | None = None
    search_queries: list[str] = field(default_factory=list)
    query_expansion_status: str = 'not_requested'
    category: str | None = None
    answer_diagnostics: dict = field(default_factory=dict)
    corpus_chunks: list[dict] | None = field(default=None, repr=False)
    scope_sources: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    stage_metrics: dict = field(default_factory=dict)
    semantic_status: str = 'not_requested'
    recall_stats: dict = field(default_factory=dict)
    session_scoped: bool = False


from app.workflow.progress import observed

@observed('文档检索与适用范围筛选')
async def retrieve_evidence(
    query: str,
    top_k: int = 12,
    *,
    entity_ids=None,
    time_range=None,
    version=None,
    extra_chunks: list[dict] | None = None,
    knowledge_document_ids: list[str] | None = None,
) -> RetrievalResult:
    settings = get_settings()
    started = perf_counter()
    from app.rag.telemetry import measured
    session_scoped = extra_chunks is not None
    index = (
        {"chunks": extra_chunks or [], "errors": [], "fingerprint": "session"}
        if session_scoped
        else await asyncio.to_thread(get_local_index)
    )
    semantic_corpus = index
    result = RetrievalResult(query=query, normalized_query=canonicalize(query), corpus_chunks=index["chunks"], session_scoped=session_scoped)
    result.stage_metrics['index_read']={'calls':1,'failed_calls':0,'duration_ms':round((perf_counter()-started)*1000,2)}
    explicit_public = knowledge_document_ids is not None
    if not explicit_public and not session_scoped and settings.dataset_profile=='public':
        from app.rag.evidence_completion import named_source_scope
        named=named_source_scope(query,index.get('sources',{}))
        if named:
            knowledge_document_ids=named['doc_ids'];explicit_public=True
            result.diagnostics['source_scope']={'basis':'unique_literal_named_source','subject':named['subject'],'doc_ids':named['doc_ids']}
    if explicit_public:
        if session_scoped or not knowledge_document_ids or any(key not in index['sources'] for key in knowledge_document_ids):
            raise ValueError('invalid_public_document_scope')
        selected_ids = set(knowledge_document_ids)
        result.corpus_chunks = [c for c in index['chunks'] if c['doc_id'] in selected_ids]
        result.scope_sources = [{'doc_id': key, 'filename': index['sources'][key]['filename'],
                                 'title': index['sources'][key]['title'],
                                 'total_chunk_count': sum(c['doc_id'] == key for c in index['chunks'])}
                                for key in dict.fromkeys(knowledge_document_ids)]
        index = {**index, 'chunks': result.corpus_chunks}
    result.warnings = [f"{e['filename']}：{e['reason']}" for e in index["errors"]]
    result.warnings.extend(dict.fromkeys(w for c in index['chunks']
                                         for w in c.get('parsing_warnings', [])))
    if not index["chunks"]:
        result.knowledge_status = "unavailable"
        return result
    if result.warnings:
        result.knowledge_status = "partial"
    entities = set(entity_ids or [])
    browsing_summary = (session_scoped or explicit_public) and is_document_summary(query, document_context=True)
    for alias, canonical in COUNTRIES.items():
        if browsing_summary:
            break
        if alias in query or re.search(rf"\b{canonical}\b", query, re.I):
            entities.add(canonical)
    years = sorted(set(re.findall(r"(?<!\d)20\d{2}(?!\d)", query)))
    # An explicit caller time range is authoritative; multiple queried years need all versions.
    period = time_range
    if not browsing_summary and period is None and len(years) == 1:
        period = {"start": years[0] + "-01-01", "end": str(int(years[0]) + 1) + "-01-01"}
    query_parts = facets(query)
    if settings.dataset_profile == 'public':
        from app.rag.query_understanding import requested_category, search_variants
        if not session_scoped:
            result.category = requested_category(query)
        if not browsing_summary and settings.dashscope_api_key and settings.bailian_host:
            variants, result.query_expansion_status = await measured(result.stage_metrics,'rewrite',lambda:search_variants(query,index['chunks']))
            query_parts.extend(variants)
        if result.query_expansion_status in {'unavailable', 'invalid_reformulation'}:
            result.warnings.append('跨语言检索表达未生成或未通过约束检查，保留原问题检索')
    result.search_queries = query_parts
    generic_attachment_summary = browsing_summary
    recall_started = perf_counter()
    lexical_index = (None if explicit_public else index.get("lexical_index")) or build_lexical_index(index["chunks"])
    bm25 = bm25_scores(query_parts, lexical_index)
    coverage = coverage_scores(query_parts,lexical_index)
    max_bm25 = max(bm25.values(), default=1) or 1
    scored = []
    for chunk in index["chunks"]:
        score = (
            1.0
            if generic_attachment_summary
            else coverage.get(chunk['chunk_id'],0.0)
        )
        metadata = chunk.get("source_metadata", {})
        source_entities = set(metadata.get("entity_ids", []))
        reason = None
        if result.category:
            from app.rag.query_understanding import source_category
            if source_category(chunk) != result.category:
                result.rejected.append({'chunk_id': chunk['chunk_id'], 'title': chunk['title'],
                                        'doc_id': chunk.get('doc_id'),
                                        'reason': '不属于用户指定的文档类别', 'metadata': metadata})
                continue
        if chunk.get('ocr_untrusted') and not browsing_summary:
            result.rejected.append({'chunk_id': chunk['chunk_id'], 'title': chunk['title'],
                                    'reason': 'OCR段落识别不可靠，不能作为回答依据', 'metadata': metadata})
            continue
        if version is not None and metadata.get("version") != version:
            reason = "文档版本与用户指定版本不匹配"
        if entities and source_entities and not entities.issubset(source_entities):
            reason = "实体不匹配"
        if period:
            start, end = metadata.get("valid_from"), metadata.get("valid_to")
            if (start and start > period["start"]) or (end and end < period["end"]):
                reason = "文档适用时间未覆盖查询范围"
        if reason:
            result.rejected.append({"chunk_id": chunk["chunk_id"], "title": chunk["title"], "reason": reason,
                                    "doc_id": chunk.get('doc_id'),
                                    "metadata": metadata})
            continue
        relevance = bm25.get(chunk["chunk_id"], 0)
        scored.append({**chunk, "score": round(.8 * score + .2 * relevance / max_bm25, 4),
                       "lexical_score": round(score, 4), "bm25_score": round(relevance, 4)})
    # A precise English name in a mixed-language question is a lexical anchor,
    # even when most Chinese attribute words do not appear in English sources.
    # This only admits retrieval candidates; the answer must still pass entailment.
    if settings.dataset_profile == 'public' and re.search(r'[\u4e00-\u9fff]', query):
        anchor_text = ' '.join([query, *query_parts[1:]]) if result.query_expansion_status == 'applied' else query
        anchors = set(re.findall(r'\b[A-Za-z][A-Za-z_.-]{3,}\b', anchor_text.lower())) - {'what', 'which', 'this', 'that', 'according'}
        # Use persisted postings to nominate potential matches, then verify body
        # text once per candidate. Retokenizing the entire corpus once per
        # anchor dominated the measured full-corpus recall time.
        potential = set().union(*(set(lexical_index['postings'].get(a,{})) for a in anchors))
        matched = {c['chunk_id']: anchors & terms(c['text']) for c in scored if c['chunk_id'] in potential}
        frequency = {a: sum(a in found for found in matched.values()) for a in anchors}
        rare = {a for a, count in frequency.items() if 0 < count <= max(3, len(scored) // 20)}
        for item in scored:
            if rare & matched.get(item['chunk_id'],set()):
                item['lexical_score'] = max(item['lexical_score'], settings.rag_min_lexical_coverage)
                item['score'] = max(item['score'], .5 + .2 * item['bm25_score'] / max_bm25)
    if browsing_summary:
        # Summaries browse the whole selected source, never a similarity-ranked top-k.
        result.chunks = sorted(scored, key=lambda c: (str(c.get('doc_id', '')),
                               c.get('page_no') or 0, c.get('start_char', 0)))
        result.candidate_count = len(scored) + len(result.rejected)
        result.mode = 'document_browse'
        if result.rejected:
            result.knowledge_status = 'partial'
            result.warnings.append('指定范围排除了部分正文，摘要仅覆盖适用内容')
        return result
    # Score ALL applicable chunks: semantic recall must not depend on lexical overlap.
    vector_path = settings.indexes_path / "knowledge_vectors.npz"
    result.semantic_status = 'session_not_indexed' if session_scoped else 'missing' if not vector_path.exists() else 'not_configured'
    if not session_scoped and vector_path.exists() and settings.dashscope_api_key and settings.bailian_host and scored:
        try:
            import numpy as np
            from app.clients.bailian_embedding import embed_texts
            from app.rag.semantic_index import load_semantic_index
            metadata,vectors = await asyncio.to_thread(load_semantic_index,semantic_corpus)
            query_vector = np.asarray((await measured(result.stage_metrics,'query_embedding',lambda:embed_texts([query])))[0], dtype=np.float32)
            if (query_vector.shape != (vectors.shape[1],) or not np.isfinite(query_vector).all()
                    or np.linalg.norm(query_vector) <= 1e-12):
                raise ValueError("查询向量维度或数值不匹配")
            query_vector /= max(float(np.linalg.norm(query_vector)), 1e-12)
            scores = dict(zip(metadata["chunk_ids"], (vectors @ query_vector).tolist()))
            for item in scored:
                item['vector_score'] = float(scores.get(item['chunk_id'], 0))
            result.mode = "hybrid"
            result.semantic_status = 'applied'
        except Exception:
            result.semantic_status = 'fallback'
            result.warnings.append("向量检索不可用或索引不匹配，已使用本地关键词检索")
    applicable_count = len(scored)
    applicable = scored
    scored = [c for c in scored if c['lexical_score'] >= settings.rag_min_lexical_coverage
              or c.get('vector_score', -1) >= settings.rag_min_vector_similarity]
    source_ids = {c.get('doc_id') for c in applicable}
    if (not scored and settings.dataset_profile == 'public'
            and settings.dashscope_api_key and settings.bailian_host
            and (session_scoped or explicit_public) and len(source_ids) == 1 and None not in source_ids
            and 0 < len(applicable) <= 60 and sum(len(c['text']) for c in applicable) <= 30000):
        # A given short document can be inspected when synonyms fail lexical
        # recall. These are ONLY candidates: generation and independent source
        # checks still decide if any sentence answers the question. No cross-file
        # or metadata/OCR bypass, no pretend vectors and no global threshold drop.
        scored = applicable
        result.mode = 'scoped_semantic_candidates'
        result.diagnostics['scoped_recall'] = {
            'basis': '小型明确单文档词法未命中，提交适用正文候选供模型核验',
            'document_count': 1, 'candidate_count': len(applicable), 'lexical_matches': 0}
    eligible_count = len(scored)
    result.candidate_count = len(scored) + len(result.rejected)
    # Rank fusion compares rankings rather than incompatible BM25/cosine scales.
    if result.mode=='hybrid':
        lexical = sorted((c for c in scored if c['lexical_score']>=settings.rag_min_lexical_coverage),key=lambda c:(-c['bm25_score'],c['chunk_id']))
        semantic = sorted((c for c in scored if c.get('vector_score',-1)>=settings.rag_min_vector_similarity),key=lambda c:(-c['vector_score'],c['chunk_id']))
        result.recall_stats = {'lexical_eligible':len(lexical),'semantic_eligible':len(semantic),
            'lexical_top_ids':[c['chunk_id'] for c in lexical[:20]],
            'semantic_top_ids':[c['chunk_id'] for c in semantic[:20]],
            'branch_pool_limit':settings.rag_fusion_pool}
        ranks = {}
        for branch in (lexical[:settings.rag_fusion_pool],semantic[:settings.rag_fusion_pool]):
            for rank,c in enumerate(branch,1):
                ranks[c['chunk_id']] = ranks.get(c['chunk_id'],0)+1/(60+rank)
        scored = [{**c,'score':ranks[c['chunk_id']],'fusion_method':'rrf_k60'} for c in scored if c['chunk_id'] in ranks]
        result.recall_stats['fusion_candidates'] = len(scored)
    scored.sort(key=lambda c: (-c["score"], c["chunk_id"]))
    result.stage_metrics['recall']={'calls':1,'failed_calls':0,'duration_ms':round(max(0,
        (perf_counter()-recall_started)*1000-result.stage_metrics.get('query_embedding',{}).get('duration_ms',0)),2)}
    if settings.enable_rerank and settings.dashscope_api_key and settings.bailian_host and scored:
        try:
            from app.clients.bailian_rerank import rerank
            # Bound cost without discarding the remaining evidence or hiding conflicts.
            pool = scored[:50]
            order = await measured(result.stage_metrics,'rerank',lambda:rerank(query,[c['text'] for c in pool],top_n=len(pool)))
            if any(type(i) is not int or i < 0 or i >= len(pool) for i in order) or len(set(order)) != len(order) or not order:
                raise ValueError('无效的重排序索引')
            scored = [pool[i] for i in order] + [c for i, c in enumerate(pool) if i not in order] + scored[50:]
            result.rerank_status = 'applied'
        except Exception:
            result.rerank_status = 'fallback'
            result.warnings.append('模型重排序不可用或返回无效结果，已保留原召回排序')
    result.chunks = scored[:top_k]
    if any(c.get("extraction_method") == "ocr" for c in result.chunks):
        result.warnings.append("包含OCR识别文本，引用定位对应识别结果；数字与规则请对照扫描原件复核")
    if any(c.get("context_limited") for c in result.chunks):
        result.diagnostics['split_chunks']=[c['chunk_id'] for c in result.chunks if c.get('context_limited')]
    result.diagnostics['retrieval_budget']={'applicable':applicable_count,'matched':len(scored),'selected':len(result.chunks),'top_k':top_k}
    if len(scored) > top_k:
        result.diagnostics['candidate_selection']='按相关度选择候选，是否足以回答由后续问题级核验判断'
        result.warnings.append("部分片段因长度预算截断，不能确认跨片段前提与限制条件完整")
        result.knowledge_status = "partial"
    if eligible_count > top_k:
        result.warnings.append(f"相关片段共{eligible_count}个，本次检查前{top_k}个；无法保证覆盖全部证据")
        result.knowledge_status = "partial"
    return result


async def retrieve(query: str, top_k: int = 5, extra_chunks: list[dict] | None = None) -> list[dict]:
    return (await retrieve_evidence(query, top_k, extra_chunks=extra_chunks)).chunks
