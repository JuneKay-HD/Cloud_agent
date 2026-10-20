"""Bounded context retention around a matched sentence, with auditable reasons."""
import re

MAX_CONTEXT_SENTENCES = 8
MAX_CONTEXT_CHARS = 1800


def complete_context(selected, chunks, sentence_reader):
    result = {(c["chunk_id"], c["start_in_chunk"]): dict(c, role="direct_support") for c in selected}
    limited = []
    for chunk in chunks:
        anchors = [c for c in selected if c["chunk_id"] == chunk["chunk_id"]]
        if not anchors:
            continue
        candidates = list(sentence_reader(chunk))
        needed = {i for i, c in enumerate(candidates) if any(c["start_in_chunk"] == a["start_in_chunk"] for a in anchors)}
        # Resolve local referents before quoting a short dependent statement alone.
        for i in list(needed):
            if i > 0 and re.search(r"同名|分别|上述|前述|该(?:项|规则|条件)|此(?:项|规则|条件)", candidates[i]["quote"]):
                needed.add(i - 1)
        # Preserve following exceptions, prerequisites and prohibitions, even when they
        # carry little lexical overlap. Stay within the same paragraph/chunk.
        for i, candidate in enumerate(candidates):
            if i > 0 and i - 1 in needed and re.match(
                r"(?:但|但是|不过|仅|只有|除外|除非|例外|不适用|不包括|不允许|不得|不能|不应|严禁|需|必须|应当|前提|否则|其中|同名|这(?:是|些)|该|上述|注意)",
                candidate["quote"],
            ):
                needed.add(i)
        length = 0
        for n, i in enumerate(sorted(needed)):
            candidate = candidates[i]
            key = (candidate["chunk_id"], candidate["start_in_chunk"])
            length += len(candidate["quote"])
            if n >= MAX_CONTEXT_SENTENCES or length > MAX_CONTEXT_CHARS:
                if chunk["chunk_id"] not in limited:
                    limited.append(chunk["chunk_id"])
                continue
            if key not in result:
                result[key] = dict(candidate, role="context", reason="保留相邻前提、指代或限制条件")
    order = {c["chunk_id"]: i for i, c in enumerate(chunks)}
    return sorted(result.values(), key=lambda c: (order[c["chunk_id"]], c["start_in_chunk"])), limited
