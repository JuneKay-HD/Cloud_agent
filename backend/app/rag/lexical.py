"""Persisted postings and BM25 statistics over real document content."""
import math
import re
from collections import Counter

from app.rag.language import canonicalize

STOP = ["请问", "告诉我", "帮我", "根据", "查询", "总结", "说明", "什么是", "是什么", "有什么",
        "是多少", "是怎样", "如何", "是否", "哪些", "怎么", "规定", "规则", "定义", "口径",
        "统计", "当前", "这个", "的", "吗", "呢"]


def normalize(text: str) -> str:
    text = canonicalize(text)
    for word in STOP:
        text = text.replace(word, "")
    return re.sub(r"\s+", "", text).lower()


def tokens(text: str) -> list[str]:
    # normalize() compacts Chinese text; preserve English word boundaries first.
    text = re.sub(r'(?<=[A-Za-z])\s+(?=[A-Za-z])', '.', text)
    text = normalize(text)
    stop = {'a','an','the','is','are','was','were','to','of','in','and','or','for','which','what',
            'from','this','that','do','does','did','can','could','with','by','as','about'}
    result = [word for word in re.findall(r"[a-z_]+", text) if word not in stop]
    for word in re.findall(r"[\u4e00-\u9fff]+", text):
        result.extend(word[i:i + 2] for i in range(len(word) - 1))
        if len(word) == 1 and word not in {"年", "和", "及", "是", "有"}:
            result.append(word)
    return result


def terms(text: str) -> set[str]:
    return set(tokens(text))


def build_lexical_index(chunks: list[dict]) -> dict:
    postings, lengths = {}, {}
    for chunk in chunks:
        from app.rag.search_text import search_text
        counts = Counter(tokens(search_text(chunk) + " " + chunk["section"] + " " + chunk["title"]))
        identifier = chunk["chunk_id"]
        lengths[identifier] = sum(counts.values())
        for term, frequency in counts.items():
            postings.setdefault(term, {})[identifier] = frequency
    return {"algorithm": "bm25", "postings": postings, "lengths": lengths,
            "average_length": sum(lengths.values()) / max(1, len(lengths))}


def bm25_scores(queries: list[str], index: dict) -> dict[str, float]:
    scores = {}
    lengths, postings = index["lengths"], index["postings"]
    count = len(lengths)
    average = max(1, index["average_length"])
    for term in set().union(*(terms(q) for q in queries)):
        matches = postings.get(term, {})
        idf = math.log(1 + (count - len(matches) + .5) / (len(matches) + .5))
        for identifier, frequency in matches.items():
            norm = frequency + 1.5 * (.25 + .75 * lengths[identifier] / average)
            scores[identifier] = scores.get(identifier, 0) + idf * frequency * 2.5 / norm
    return scores


def coverage_scores(queries: list[str], index: dict) -> dict[str, float]:
    """Exact set coverage from persisted postings; avoid retokenizing the corpus."""
    scores={}
    for query in queries:
        query_terms=terms(query)
        if not query_terms:continue
        counts=Counter()
        for term in query_terms:
            counts.update(index['postings'].get(term,{}).keys())
        for identifier,count in counts.items():
            scores[identifier]=max(scores.get(identifier,0),count/len(query_terms))
    return scores
def update_lexical_index(index: dict | None, added: list[dict], removed: list[dict]) -> dict:
    """Copy only touched postings; never mutate the shared persisted snapshot."""
    if index is None:
        return build_lexical_index(added)
    from app.rag.search_text import search_text
    postings, lengths = dict(index["postings"]), dict(index["lengths"])
    touched = {}
    for chunk in removed:
        identifier = chunk["chunk_id"]
        lengths.pop(identifier, None)
        text = search_text(chunk) + " " + chunk["section"] + " " + chunk["title"]
        for term in terms(text):
            if term not in touched:
                touched[term] = dict(postings.get(term, {}))
            values = touched[term]
            values.pop(identifier, None)
    addition = build_lexical_index(added)
    lengths.update(addition["lengths"])
    for term, matches in addition["postings"].items():
        if term not in touched:
            touched[term] = dict(postings.get(term, {}))
        values = touched[term]
        values.update(matches)
    for term, values in touched.items():
        if values:
            postings[term] = values
        else:
            postings.pop(term, None)
    return {"algorithm": "bm25", "postings": postings, "lengths": lengths,
            "average_length": sum(lengths.values()) / max(1, len(lengths))}
