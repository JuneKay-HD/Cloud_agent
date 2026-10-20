"""Import-time layout region side table.

Maps original file -> pages -> line/block regions (real coordinates) -> chunk
bindings, so citation clicks read confirmed mappings instead of re-searching a
whole page on every request. Stores are keyed by original file hash AND page
text version: a text repair creates a new store file, the old one stays
readable for historical citations (rollback by construction).

Design: reports/CITATION_REGION_HIGHLIGHT_PROPOSAL_20261015.md and
reports/CITATION_LOCATION_AND_BUDGET_PLAN_20261015.md §3.2. No placeholder
positions (unlike RAGFlow's non-PDF fallback branch): when real coordinates
cannot be produced the page records an explicit status and reason instead.

Coordinates: PDF points, visible-page top-left frame (same space as the
legacy percentage highlight boxes and the /page PNG render). Page rotation is
recorded; the viewer rotates rectangles itself around this frame.
"""
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

REGION_LAYOUT_VERSION = 'regions-v1'
PARSER_VERSION = 'region-builder-v1'

# Location confidence levels, most certain first. `candidate` and `none` are
# never drawn as reliable highlights.
LEVELS = ('exact', 'region', 'partial', 'candidate', 'none')

LEVEL_NOTES = {
    'exact': '引用文字已在原页逐行精确定位。',
    'region': '引用属于已确认的原页段落区域，未逐字定位。',
    'partial': '仅部分引用文字完成定位，未覆盖片段已单独标注。',
    'candidate': '存在多个候选区域，需人工核对，未计为成功定位。',
    'none': '没有可靠原页区域。',
}


def text_digest(text: str) -> str:
    return sha256((text or '').encode()).hexdigest()


def text_fingerprint(pages) -> str:
    """Version identity of the page texts the spans bind into."""
    parts = [f"{page.get('page_no')}:{text_digest(page.get('text', ''))}" for page in pages]
    return sha256('\x00'.join(parts).encode()).hexdigest()


def store_path(base_dir, original_hash: str, text_hash: str) -> Path:
    return Path(base_dir) / original_hash / f'{text_hash[:16]}-{REGION_LAYOUT_VERSION}.json'


def new_store(original_hash: str, text_hash: str, builder: str, doc_id=None) -> dict:
    return {
        'region_layout_version': REGION_LAYOUT_VERSION,
        'parser_version': PARSER_VERSION,
        'doc_id': doc_id,
        'original_hash': original_hash,
        'text_hash': text_hash,
        'builder': builder,
        'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'pages': [],
        'chunks': {},
    }


def _valid_bbox(bbox, width, height) -> bool:
    return (isinstance(bbox, list) and len(bbox) == 4
            and all(isinstance(v, (int, float)) and v == v for v in bbox)
            and 0 <= bbox[0] < bbox[2] <= width + .5
            and 0 <= bbox[1] < bbox[3] <= height + .5)


def validate_store(store, original_hash: str, text_hash: str = None) -> bool:
    """Only a structurally sound store for this exact original may be used.

    Hard rules: non-annotation regions must carry a valid in-page bbox; an
    exact span may only reference line regions bound to page-text offsets.
    Degenerate/placeholder geometry can never pass validation.
    """
    if not isinstance(store, dict) or store.get('region_layout_version') != REGION_LAYOUT_VERSION:
        return False
    if store.get('original_hash') != original_hash:
        return False
    if text_hash is not None and store.get('text_hash') != text_hash:
        return False
    if not isinstance(store.get('pages'), list) or not isinstance(store.get('chunks'), dict):
        return False
    for page in store['pages']:
        if not isinstance(page.get('page_no'), int) or not isinstance(page.get('regions'), list):
            return False
        width, height = page.get('width', 0), page.get('height', 0)
        # A page whose geometry failed keeps an honest empty record instead.
        if page['regions'] and (width <= 0 or height <= 0):
            return False
        lines = {r.get('region_id') for r in page['regions'] if r.get('kind') == 'line'}
        for region in page['regions']:
            if region.get('kind') == 'annotation':
                continue
            if not _valid_bbox(region.get('bbox'), width, height):
                return False
        for chunk in store['chunks'].values():
            if chunk.get('page_no') != page['page_no']:
                continue
            for span in chunk.get('spans', []):
                if span.get('level') == 'exact' and not set(span.get('region_ids', [])) <= lines:
                    return False
    return True


def load_store(base_dir, original_hash: str, text_hash: str = None):
    base = Path(base_dir) / original_hash
    if not base.is_dir():
        return None
    candidates = [store_path(base_dir, original_hash, text_hash)] if text_hash else sorted(base.glob(f'*-{REGION_LAYOUT_VERSION}.json'))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            store = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if validate_store(store, original_hash, text_hash):
            return store
    return None


def save_store(base_dir, store) -> Path:
    if not validate_store(store, store.get('original_hash', ''), store.get('text_hash')):
        raise ValueError('区域侧表结构无效，未写入')
    target = store_path(base_dir, store['original_hash'], store['text_hash'])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(store, ensure_ascii=False), encoding='utf-8')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def page_record(store, page_no: int):
    return next((p for p in store.get('pages', []) if p.get('page_no') == page_no), None)


def page_text_matches(page, page_text: str) -> bool:
    """Coordinates are only valid for the text they were aligned to."""
    return bool(page) and page.get('page_text_sha256') == text_digest(page_text)


def set_chunk_binding(store, chunk: dict, level: str, spans, reason=None) -> None:
    """Record an import-time chunk -> region binding (never guesses)."""
    if level not in LEVELS:
        raise ValueError('未知的定位等级')
    store.setdefault('chunks', {})[chunk['chunk_id']] = {
        'page_no': chunk.get('page_no'), 'level': level, 'spans': spans, 'reason': reason}


def chunk_binding(store, chunk_id: str):
    return store.get('chunks', {}).get(chunk_id)


def _non_space_between(text, start, end, exclusions=()):
    excluded = set()
    for ex_start, ex_end in exclusions:
        excluded.update(range(max(ex_start, start), min(ex_end, end)))
    return sum(1 for i in range(start, end) if i not in excluded and not text[i].isspace())


def _clip_line(line, start, end):
    """Rect of the span's portion of one bound line; None when untouched."""
    offsets = line.get('char_offsets') or []
    boxes = line.get('char_boxes') or []
    picked = [boxes[i] for i, off in enumerate(offsets)
              if 0 <= off and start <= off < end and i < len(boxes)]
    if not picked:
        return None
    return [min(b[0] for b in picked), line['bbox'][1],
            max(b[1] for b in picked), line['bbox'][3]]


def regions_for_span(page, text, start_char: int, end_char: int, quote: str = None) -> dict:
    """Resolve a validated page-text span to region geometry with a level.

    exact: every non-space span char is proven by bound line geometry;
    partial: only the covered sub-spans are reported (uncovered ranges listed);
    region/candidate: the span text lives in exactly one / several blocks;
    none: nothing reliable. Candidates are data for review, never a highlight.
    """
    lines = [r for r in page.get('regions', []) if r.get('kind') == 'line']
    blocks = [r for r in page.get('regions', []) if r.get('kind') in ('block', 'table', 'heading')]
    exclusions = [tuple(a) for a in page.get('annotation_spans', [])]
    excluded_offsets = {i for a, b in exclusions for i in range(max(a, start_char), min(b, end_char))}
    result = {'level': 'none', 'level_note': LEVEL_NOTES['none'], 'spans': [],
              'uncovered': [], 'candidates': []}

    total = _non_space_between(text, start_char, end_char, exclusions)
    covered = set()
    for line in lines:
        for offset in line.get('char_offsets') or []:
            if start_char <= offset < end_char and offset >= 0 and offset not in excluded_offsets:
                covered.add(offset)
    runs = []
    for offset in sorted(covered):
        if runs and all(text[i].isspace() or i in excluded_offsets
                        for i in range(runs[-1][1], offset)):
            runs[-1][1] = offset + 1
        else:
            runs.append([offset, offset + 1])
    spans = []
    for a, b in runs:
        rects, ids = [], []
        for line in lines:
            rect = _clip_line(line, a, b)
            if rect is not None:
                rects.append(rect)
                ids.append(line['region_id'])
        if rects:
            spans.append({'start_char': a, 'end_char': b, 'level': 'exact',
                          'region_ids': ids, 'rects': rects})
    if total and len(covered) == total and spans:
        result.update(level='exact', level_note=LEVEL_NOTES['exact'], spans=spans)
        return result
    if spans:
        gaps = []
        cursor = start_char
        for a, b in runs:
            if _non_space_between(text, cursor, a, exclusions):
                gaps.append([cursor, a])
            cursor = max(cursor, b)
        if _non_space_between(text, cursor, end_char, exclusions):
            gaps.append([cursor, end_char])
        result.update(level='partial', level_note=LEVEL_NOTES['partial'],
                      spans=spans, uncovered=gaps)
        return result

    # Paragraph fallback: normalized span text inside block line flows.
    from app.rag.source_location import normalized
    target, _ = normalized(text[start_char:end_char])
    if target:
        containing = []
        for block in blocks:
            block_lines = {l['region_id']: l for l in lines}
            joined = ''.join(block_lines[i]['text'] for i in block.get('line_ids', []) if i in block_lines)
            if target in normalized(joined)[0]:
                containing.append(block)
        if len(containing) == 1:
            result.update(level='region', level_note=LEVEL_NOTES['region'],
                          spans=[{'start_char': start_char, 'end_char': end_char, 'level': 'region',
                                  'region_ids': [containing[0]['region_id']],
                                  'rects': [containing[0]['bbox']]}])
            return result
        if containing:
            result.update(level='candidate', level_note=LEVEL_NOTES['candidate'],
                          candidates=[{'region_ids': [b['region_id']], 'rects': [b['bbox']]}
                                      for b in containing])
            return result
    return result


def percent_rects(page, rects):
    """Convert PDF-point rects (visible top-left frame) to legacy 0-100 boxes."""
    width, height = page.get('width', 0), page.get('height', 0)
    if width <= 0 or height <= 0:
        return []
    boxes = []
    for x0, top, x1, bottom in rects:
        boxes.append({'left': x0 / width * 100, 'top': top / height * 100,
                      'width': (x1 - x0) / width * 100, 'height': (bottom - top) / height * 100})
    return boxes

