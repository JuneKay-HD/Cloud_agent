"""Task-specific evidence allocation; trim only whole measured sentences."""
import json
import re

from app.rag.search_text import search_text
from app.rag.lexical import terms


def task_kind(question, retrieval):
    from app.rag.answerer import is_source_search
    from app.rag.summary_intent import is_document_summary
    if is_source_search(question):
        return 'source_search'
    if is_document_summary(question,document_context=bool(retrieval.scope_sources)):
        return 'single_document_summary' if len(retrieval.scope_sources)==1 else 'cross_document_synthesis'
    if re.search(r'比较|对比|综合|异同|各自|compare|across',question,re.I):
        return 'cross_document_synthesis'
    return 'point_answer'


def allocate_sentences(question, retrieval, chunks):
    from app.rag.answerer import sentences
    kind = task_kind(question,retrieval)
    max_chars = 54000 if kind=='cross_document_synthesis' else 36000
    from app.rag.retriever import facets
    aspects = facets(question)[:8]
    by_doc = {}
    query_terms = [terms(q) for q in [question,*retrieval.search_queries,*aspects]]
    for rank,chunk in enumerate(chunks):
        entries = []
        for sentence in sentences(chunk):
            if len(sentence['quote'])>5000:
                continue
            matched = terms(search_text({'text':sentence['quote']}))
            score = max((len(q & matched)/max(1,len(q)) for q in query_terms),default=0)
            entries.append((score,rank,sentence))
        # Keep local order inside each selected chunk; put best chunks first.
        by_doc.setdefault(chunk.get('doc_id',chunk['chunk_id']),[]).extend(entries)
    for entries in by_doc.values():
        entries.sort(key=lambda row:(-row[0],row[1],row[2]['start_in_chunk']))
    selected = []
    size = 0
    # Round-robin across sources prevents the first document exhausting context.
    while any(by_doc.values()) and len(selected)<180:
        for entries in by_doc.values():
            if not entries or len(selected)>=180:
                continue
            _,_,sentence = entries.pop(0)
            cost = len(json.dumps(sentence,ensure_ascii=False)) + 300
            if size + cost > max_chars:
                continue
            selected.append(sentence); size += cost
    total = sum(1 for chunk in chunks for sentence in sentences(chunk))
    return selected, {'task_kind':kind,'max_context_chars':max_chars,'max_support_sentences':16,
                      'available_sentences':total,'selected_sentences':len(selected),
                      'context_truncated':len(selected)<total,'aspects':aspects}
