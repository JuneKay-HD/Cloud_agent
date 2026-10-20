"""Heading-aware evidence chunks with exact offsets into extracted page text."""
import hashlib
import re
from pathlib import Path
from app.local_paths import readable_path


def chunk_pages(path: Path, pages: list[dict], max_chars: int = 700, *, hard_limit: bool = False) -> list[dict]:
    if max_chars < 1:
        raise ValueError("max_chars必须为正整数")
    chunks = []
    document_hash = hashlib.sha256(readable_path(path).read_bytes()).hexdigest()
    doc_id = hashlib.sha256((path.name + document_hash).encode()).hexdigest()[:24]
    for page in pages:
        text = page["text"]
        heading = path.stem
        # A heading without a following blank line must not swallow its body.
        pattern = r"^[ \t]*#{1,6}[ \t]+[^\n]*|^(?![ \t]*#{1,6}[ \t])[^\n]+(?:\n(?![ \t]*\n|[ \t]*#{1,6}[ \t])[^\n]+)*"
        for block in re.finditer(pattern, text, re.MULTILINE):
            raw = block.group()
            if re.match(r'^[ \t]*#{1,6}[ \t]+', raw):
                heading = raw.lstrip("# ").splitlines()[0].strip()
                continue
            start, end = block.start(), block.end()
            while start < end:
                stop = min(start + max_chars, end)
                if stop < end:
                    boundary = max(text.rfind("。", start, stop), text.rfind("\n", start, stop))
                    if boundary > start:
                        stop = boundary + 1
                    else:
                        # Never cut a sentence before a possible trailing negation.
                        if not hard_limit:
                            following = re.search(r"[。！？\n]", text[stop:end])
                            stop = stop + following.end() if following else end
                context_limited = start > block.start() or stop < end
                while start < stop and text[start].isspace():
                    start += 1
                while stop > start and text[stop - 1].isspace():
                    stop -= 1
                if stop <= start:
                    break
                value = text[start:stop]
                chunk_id = hashlib.sha256(f"{doc_id}:{page['page_no']}:{start}:{stop}".encode()).hexdigest()[:24]
                chunks.append({
                    "chunk_id": chunk_id, "doc_id": doc_id, "title": path.stem,
                    "filename": path.name, "section": heading, "page_no": page["page_no"],
                    "start_char": start, "end_char": stop, "text": value,
                    "source_hash": document_hash,
                    "extraction_method": page.get("extraction_method", "text"),
                    "column_count": max(0, len(page.get("column_boundaries", []))-1),
                    "ocr_min_confidence": page.get("ocr_min_confidence"),
                    "text_format": page.get("text_format", "text"),
                    "ocr_engine": page.get("ocr_engine"),
                    "ocr_validation": page.get("ocr_validation") or (
                        'unverified_visual_transcription' if page.get('embedded_images') else None),
                    "ocr_untrusted": any(r['start_char'] < stop and r['end_char'] > start
                                         for r in page.get('ocr_uncertain_ranges', [])),
                    "parsing_warnings": page.get('parsing_warnings', []),
                    "context_limited": context_limited,
                })
                start = stop
    return chunks
