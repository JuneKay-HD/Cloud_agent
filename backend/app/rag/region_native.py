"""Native-PDF region builder: real line/block geometry bound to page text.

Reuses the battle-tested `source_location._layout` char cache (crop- and
rotation-normalized, PDF points, visible-page top-left frame) and binds each
visual line back to its page-text span with a conservative alignment: only
globally unique or cursor-unambiguous line text is bound; everything else
stays unbound geometry that can still serve paragraph-level (`region`)
fallbacks but never produces exact boxes. Table and annotation markup added
by the parsers is excluded from exact coverage accounting.
"""
from hashlib import sha256
from pathlib import Path

from app.rag import source_location
from app.rag.region_store import (new_store, page_record as get_page, set_chunk_binding,
                                  text_digest, text_fingerprint)
from app.rag.source_location import _layout, hits, normalized, rows

import re

MARKER = re.compile(r'\[(?:页面标题及图注文字|区域(\d+)第(\d+)栏|表格(\d+)|独立表格(\d+))[：:][^\]]*\]')


def _line_runs(chars):
    """Visual lines: rows split on measured horizontal gaps (mirrors _rectangles)."""
    lines = []
    for row in rows(chars):
        current = None
        for index in row:
            char = chars[index]
            gap = char['x0'] - current['x1'] if current else 0
            if current is not None and not (-1 <= gap <= max(6, char['bottom'] - char['top'])):
                lines.append(current)
                current = None
            if current is None:
                current = {'indices': [index], 'x0': char['x0'], 'x1': char['x1'],
                           'top': char['top'], 'bottom': char['bottom']}
            else:
                current['indices'].append(index)
                current['x1'] = max(current['x1'], char['x1'])
                current['top'] = min(current['top'], char['top'])
                current['bottom'] = max(current['bottom'], char['bottom'])
        if current is not None:
            lines.append(current)
    return [line for line in lines
            if any(not chars[i]['text'].isspace() for i in line['indices'])]


def _segments(text):
    """Split parser text into marker-annotated spans and body spans."""
    marks = list(MARKER.finditer(text))
    segments = []
    cursor = 0
    for number, mark in enumerate(marks):
        if mark.start() > cursor:
            segments.append({'kind': 'prose', 'start_char': cursor, 'end_char': mark.start()})
        band, column = (int(mark.group(1)), int(mark.group(2))) if mark.group(1) else (None, None)
        if mark.group(1):
            kind, cell = 'column', (band, column)
        elif mark.group(3) or mark.group(4):
            kind, cell = 'table', None
        else:
            kind, cell = 'heading', None
        end = marks[number + 1].start() if number + 1 < len(marks) else len(text)
        segments.append({'kind': kind, 'cell': cell, 'marker': mark.group(),
                         'marker_span': (mark.start(), mark.end()),
                         'start_char': mark.end(), 'end_char': end})
        cursor = end
    if not marks and text:
        segments.append({'kind': 'prose', 'start_char': 0, 'end_char': len(text)})
    return segments


def _order_lines(lines, cells):
    """Reading order: column cells band-by-band when a layout exists."""
    if not cells:
        return sorted(lines, key=lambda l: (l['top'], l['x0']))

    def key(line):
        cx = (line['x0'] + line['x1']) / 2
        for band_index, (top, bottom, boundaries) in enumerate(cells):
            if top <= line['top'] < bottom:
                for column in range(len(boundaries) - 1):
                    if boundaries[column] <= cx < boundaries[column + 1]:
                        return (1, band_index, column, line['top'], line['x0'])
        return (0, 0, 0, line['top'], line['x0'])
    return sorted(lines, key=key)


DAMAGED = re.compile(r'[�\x00-\x08\x0b\x0c\x0e-\x1f]')
MIN_RUN = 4  # Shorter pieces cannot be uniquely verified; they stay unbound.


def _bind_piece(stream, offsets, piece, cursor, base, line, char_index, run_start):
    """Bind one clean run of a line into the segment stream. None on failure."""
    candidates = [(piece, run_start, len(piece))]
    # A measured wrap hyphen: letter + trailing '-', and the bound prefix must
    # be followed by more letters in the page text.
    if len(piece) >= 2 and piece.endswith('-') and piece[-2].isalpha():
        candidates.append((piece[:-1], run_start, len(piece) - 1))
    for candidate, start_at, usable in candidates:
        occurrences = hits(stream, candidate)
        chosen = None
        if len(occurrences) == 1:
            chosen = occurrences[0]
        elif occurrences:
            after = [o for o in occurrences if o >= cursor]
            if len(after) == 1 and len(candidate) >= 8:
                chosen = after[0]
        if chosen is None:
            continue
        if usable < len(piece):
            following = chosen + usable
            if following >= len(stream) or not stream[following].isalpha():
                continue
        for position in range(start_at, start_at + usable):
            line['char_offsets'][char_index[position]] = base + offsets[chosen + position - start_at]
        return chosen + usable
    return None


def _align(lines, text, segments):
    """Bind line geometry to page-text spans; ambiguous lines stay unbound.

    A line binds per maximal clean run: damaged glyphs (U+FFFD, controls)
    split the line and each run must verify on its own, so one bad glyph can
    never take down the whole line and never gets silently covered.
    """
    for segment in segments:
        body = text[segment['start_char']:segment['end_char']]
        stream, offsets = normalized(body)
        if not stream:
            continue
        cursor = 0
        for line in lines:
            if line.get('start_char') is not None:
                continue
            target, char_index = normalized(line['text'])
            if len(target) < 1:
                continue
            line['char_offsets'] = [-1] * len(line['text'])
            position = 0
            for piece in DAMAGED.split(target):
                run_start, position = position, position + len(piece)
                if len(piece) < MIN_RUN:
                    continue
                new_cursor = _bind_piece(stream, offsets, piece, cursor, base=segment['start_char'],
                                         line=line, char_index=char_index, run_start=run_start)
                if new_cursor is not None:
                    cursor = new_cursor
            bound_offsets = [o for o in line['char_offsets'] if o >= 0]
            if bound_offsets:
                line['start_char'] = min(bound_offsets)
                line['end_char'] = max(bound_offsets) + 1
                line['segment'] = segment['kind']
    return lines


def _non_space(text, start, end, exclusions=()):
    excluded = set()
    for ex_start, ex_end in exclusions:
        excluded.update(range(max(ex_start, start), min(ex_end, end)))
    return sum(1 for i in range(start, end) if i not in excluded and not text[i].isspace())


def _covered_offsets(lines, start, end, excluded=()):
    """Per-char verification: page-text offsets proven by bound line geometry."""
    blocked = set(excluded)
    covered = set()
    for line in lines:
        for offset in line.get('char_offsets') or []:
            if start <= offset < end and offset >= 0 and offset not in blocked:
                covered.add(offset)
    return covered


def _offset_runs(offsets, text, excluded=()):
    """Maximal covered runs; merge across gaps holding only whitespace/markup."""
    blocked = set(excluded)
    runs = []
    for offset in sorted(offsets):
        if runs and all(text[i].isspace() or i in blocked for i in range(runs[-1][1], offset)):
            runs[-1][1] = offset + 1
        else:
            runs.append([offset, offset + 1])
    return [(a, b) for a, b in runs]


def _visible_chars(chars, width, height):
    """Clip char geometry to the rendered frame; drop what the page never shows."""
    kept = []
    for char in chars:
        x0, top = max(char['x0'], 0.0), max(char['top'], 0.0)
        x1, bottom = min(char['x1'], width), min(char['bottom'], height)
        if x1 - x0 > 0.1 and bottom - top > 0.1:
            kept.append({**char, 'x0': x0, 'x1': x1, 'top': top, 'bottom': bottom})
    return kept


def build_page_regions(pdf, pdf_path, page, cache_dir=None):
    """One page's region record; honest empty record when geometry fails."""
    page_no, text = page['page_no'], page.get('text', '')
    layout, _ = _layout(pdf_path, page_no, cache_dir)
    record = {'page_no': page_no, 'width': layout.get('width') or 0, 'height': layout.get('height') or 0,
              'rotation': layout.get('rotation') or 0, 'page_text_sha256': text_digest(text),
              'map_status': 'unavailable', 'status_reason': layout.get('reason'),
              'annotation_spans': [], 'regions': []}
    if not text.strip():
        record.update(map_status='empty', status_reason=None)
        return record
    chars = layout.get('chars') or []
    if layout.get('reason') or not chars:
        record['status_reason'] = layout.get('reason') or 'no_text_layer'
        return record
    chars = _visible_chars(chars, layout['width'], layout['height'])
    if not chars:
        record['status_reason'] = 'no_text_layer'
        return record

    cells = None
    regions = page.get('column_regions')
    if regions:
        origin_x, origin_y = layout['visible_bbox'][0], layout['visible_bbox'][1]
        cells = [(r['top'] - origin_y, r['bottom'] - origin_y,
                  [b - origin_x for b in r['boundaries']]) for r in regions]
    elif page.get('extraction_method') == 'ohr_ground_truth' and len(text) >= 600:
        from app.rag.parsers import pdf_column_layout
        detected = pdf_column_layout(pdf.pages[page_no - 1]) if page_no <= len(pdf.pages) else None
        if detected:
            origin_x, origin_y = layout['visible_bbox'][0], layout['visible_bbox'][1]
            cells = [(r['top'] - origin_y, r['bottom'] - origin_y,
                      [b - origin_x for b in r['boundaries']]) for r in detected['column_regions']]

    raw_lines = _line_runs(chars)
    lines = []
    for order, line in enumerate(_order_lines(raw_lines, cells)):
        value = ''.join(chars[i]['text'] for i in line['indices'])
        lines.append({'region_id': f"p{page_no}-l{order:04d}", 'kind': 'line',
                      'bbox': [line['x0'], line['top'], line['x1'], line['bottom']],
                      'text': value, 'char_boxes': [[chars[i]['x0'], chars[i]['x1']] for i in line['indices']],
                      'start_char': None, 'end_char': None, 'order': order,
                      'source': 'extraction', 'confidence': None, 'parent': None})
    segments = _segments(text)
    record['annotation_spans'] = [list(s['marker_span']) for s in segments if 'marker_span' in s]
    _align(lines, text, segments)

    blocks, membered = [], set()
    def add_block(kind, members, cell=None):
        members = [l for l in members if l in lines]
        if not members:
            return
        block_id = f"p{page_no}-b{len(blocks):02d}"
        blocks.append({'region_id': block_id, 'kind': kind,
                       'bbox': [min(l['bbox'][0] for l in members), min(l['bbox'][1] for l in members),
                                max(l['bbox'][2] for l in members), max(l['bbox'][3] for l in members)],
                       'line_ids': [l['region_id'] for l in members], 'column': cell})
        for line in members:
            line['parent'] = block_id
            membered.add(id(line))

    if cells:
        for band_index, (top, bottom, boundaries) in enumerate(cells):
            for column in range(len(boundaries) - 1):
                members = [l for l in lines
                           if top <= l['bbox'][1] < bottom
                           and boundaries[column] <= (l['bbox'][0] + l['bbox'][2]) / 2 < boundaries[column + 1]]
                add_block('block', members, cell=column + 1)
        add_block('heading', [l for l in lines if id(l) not in membered and l['bbox'][1] < (cells[0][0] if cells else 0)])
    add_block('block', [l for l in lines if id(l) not in membered])

    bound = [l for l in lines if l.get('start_char') is not None]
    total = _non_space(text, 0, len(text), record['annotation_spans'])
    covered = sum(_non_space(text, l['start_char'], l['end_char'], record['annotation_spans']) for l in bound)
    record['regions'] = [*blocks, *lines]
    record['aligned_ratio'] = round(covered / total, 4) if total else None
    record['map_status'] = 'aligned' if bound else 'unbound'
    record['status_reason'] = None if bound else 'no_match'
    return record


def _block_text(page_record, block):
    lines = {l['region_id']: l for l in page_record['regions'] if l['kind'] == 'line'}
    joined = ''.join(lines[i]['text'] for i in block.get('line_ids', []) if i in lines)
    return normalized(joined)[0]


def bind_chunk(store, chunk, text) -> None:
    """Import-time chunk -> region binding; the model never invents geometry."""
    page = get_page(store, chunk.get('page_no'))
    start, end = chunk['start_char'], chunk['end_char']
    if page is None:
        set_chunk_binding(store, chunk, 'none', [], reason='missing_page')
        return
    lines = [r for r in page['regions'] if r['kind'] == 'line']
    blocks = [r for r in page['regions'] if r['kind'] in ('block', 'table', 'heading')]
    exclusions = [tuple(a) for a in page.get('annotation_spans', [])]
    excluded_offsets = {i for a, b in exclusions for i in range(max(a, start), min(b, end))}
    total = _non_space(text, start, end, exclusions)
    covered = _covered_offsets(lines, start, end, excluded_offsets)
    runs = _offset_runs(covered, text, excluded_offsets)
    if total and len(covered) == total:
        ids = [l['region_id'] for l in lines
               if any(start <= o < end for o in l.get('char_offsets') or [] if o >= 0)]
        set_chunk_binding(store, chunk, 'exact',
                          [{'start_char': start, 'end_char': end, 'level': 'exact', 'region_ids': ids}])
        return
    if covered:
        spans = [{'start_char': a, 'end_char': b, 'level': 'exact',
                  'region_ids': [l['region_id'] for l in lines
                                 if any(a <= o < b for o in l.get('char_offsets') or [] if o >= 0)]}
                 for a, b in runs]
        set_chunk_binding(store, chunk, 'partial', spans, reason='partial_coverage')
        return
    target, _ = normalized(text[start:end])
    containing = [b for b in blocks if target and target in _block_text(page, b)]
    if len(containing) == 1:
        set_chunk_binding(store, chunk, 'region',
                          [{'start_char': start, 'end_char': end, 'level': 'region',
                            'region_ids': [containing[0]['region_id']]}])
    elif containing:
        set_chunk_binding(store, chunk, 'candidate',
                          [{'start_char': start, 'end_char': end, 'level': 'candidate',
                            'region_ids': [b['region_id'] for b in containing]}], reason='ambiguous_match')
    else:
        set_chunk_binding(store, chunk, 'none', [], reason='no_match')


def build_document_store(pdf_path, pages, chunks, original_hash, *, cache_dir=None):
    """Full side table for one original PDF; never raises into the index build."""
    import pdfplumber
    pdf_path = str(pdf_path)
    text_hash = text_fingerprint(pages)
    doc_id = chunks[0].get('doc_id') if chunks else None
    store = new_store(original_hash, text_hash, 'native-pdfplumber', doc_id=doc_id)
    page_texts = {p['page_no']: p.get('text', '') for p in pages}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pages:
            store['pages'].append(build_page_regions(pdf, pdf_path, page, cache_dir))
    for chunk in chunks:
        page = next((p for p in store['pages'] if p['page_no'] == chunk.get('page_no')), None)
        bind_chunk(store, chunk, page_texts.get(chunk.get('page_no'), '') if page else '')
    return store


def original_sha256(pdf_path) -> str:
    return sha256(Path(pdf_path).read_bytes()).hexdigest()
