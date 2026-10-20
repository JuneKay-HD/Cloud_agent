"""Conservative headings from source text, never from chunk's filename fallback."""
import re
from statistics import median


def native_headings(page, text):
    """Large-font lines mapped uniquely back into the extracted source text."""
    sizes = [float(c['size']) for c in page.chars if c.get('text', '').strip() and c.get('size')]
    if not sizes:
        return []
    body = median(sizes)
    headings = []
    for line in page.extract_text_lines(return_chars=True):
        chars = [c for c in line.get('chars', []) if c.get('text', '').strip()]
        if not chars or min(float(c.get('size', 0)) for c in chars) < body * 1.2:
            continue
        value = line['text'].strip()
        if not 1 <= len(value) <= 100 or re.search(r'[。！？；]', value):
            continue
        candidates = [m for m in re.finditer(r'(?m)^[^\n]+', text)
                      if re.sub(r'\s+', '', m.group()) == re.sub(r'\s+', '', value)]
        if len(candidates) != 1:
            continue
        match = candidates[0]
        size = median(float(c['size']) for c in chars)
        headings.append({'title': re.sub(r'\s+', ' ', match.group()).strip(), 'raw_title': match.group(),
                         'start_char': match.start(), 'end_char': match.end(),
                         'level': 1000-round(size*10), 'font_size': size, 'method': 'native_font_and_exact_line'})
    return headings


def section_location(pages, chunk):
    active = []
    for page in pages:
        number = page.get('page_no')
        if number is not None and chunk.get('page_no') is not None and number > chunk['page_no']:
            break
        # Font sizes and extraction order alone cannot associate headlines with
        # articles on multi-column pages. Never nest independent stories.
        if (len(page.get('column_boundaries', [])) > 2
                or re.search(r'\[区域\d+第\d+栏[：:]', page.get('text', ''))):
            active = []
            if number == chunk.get('page_no'):
                return {'section_heading': None, 'section_path': [],
                        'section_note': '多栏页面，尚未可靠确认引用所属标题；请按页码和原文偏移核对'}
            continue
        # Vision Markdown may contain inferred structure, not original headings.
        if page.get('embedded_images') or page.get('ocr_validation') == 'unverified_visual_transcription' or page.get('extraction_method') in {'ocr', 'vision_transcription'}:
            active = []
            continue
        text = page.get('text', '')
        limit = chunk.get('start_char', 0) if number == chunk.get('page_no') else len(text)
        if page.get('headings'):
            for heading in page['headings']:
                if heading['start_char'] > limit:
                    break
                while active and active[-1]['level'] >= heading['level']:
                    active.pop()
                active.append({**heading, 'page_no': number})
            continue
        for line in re.finditer(r'(?m)^([^\n]+)', text):
            if line.start() > limit:
                break
            value = line.group().strip()
            md = re.fullmatch(r'(#{1,6})\s+(.{1,100})', value)
            chapter = re.fullmatch(r'第[一二三四五六七八九十百\d]+[章节篇部]\s*[^。！？；]{0,60}', value)
            # A numbered list alone does not establish a section hierarchy.
            if not (md or chapter):
                continue
            level = len(md[1]) if md else 1
            title = md[2] if md else value
            while active and active[-1]['level'] >= level:
                active.pop()
            active.append({'title': title, 'level': level, 'page_no': number,
                           'start_char': line.start(), 'end_char': line.end(), 'method': 'source_heading_syntax'})
    return {'section_heading': active[0] if active else None, 'section_path': active,
            'section_note': '' if active else '未识别到章节标题'}


def citation_section(pages, chunk, quote=None):
    """Resolve headings at the selected exact quote, including multi-section chunks."""
    page = next((p for p in pages if p.get('page_no') == chunk.get('page_no')), None)
    text = page.get('text', '') if page else chunk.get('text', '')
    start, end = chunk.get('start_char', 0), chunk.get('end_char', len(text))
    if quote:
        selected = text[start:end]
        offset = selected.find(quote)
        if offset < 0 or selected.find(quote, offset+len(quote)) >= 0:
            return {'section_heading': None, 'section_path': [], 'section_note': '未识别到章节标题：引用无法在片段内唯一定位',
                    'located_quote': quote, 'citation_start_char': None, 'citation_end_char': None}
        start += offset
        end = start + len(quote)
    return {**section_location(pages, {**chunk, 'start_char': start}), 'located_quote': quote,
            'citation_start_char': start, 'citation_end_char': end}
