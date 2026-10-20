"""Parse common text documents while preserving source locations."""

from pathlib import Path
import json
import re

import pdfplumber
from app.config import get_settings
from app.local_paths import readable_path

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
PRESENTATION_SUFFIXES = {'.ppt', '.pptx'}
SUPPORTED_SUFFIXES = {'.md', '.txt', '.pdf', '.docx'} | IMAGE_SUFFIXES | PRESENTATION_SUFFIXES


def pdf_table_text(page, tables=None):
    """Keep ruled table cells attached to their row/column instead of columns."""
    if tables is None:
        tables = [t for t in page.find_tables() if len(t.rows) >= 2 and len(t.columns) >= 2]
    if not tables:
        return None
    def outside_tables(obj):
        if obj.get('object_type') != 'char':
            return True
        x, y = (obj['x0']+obj['x1'])/2, (obj['top']+obj['bottom'])/2
        return not any(t.bbox[0] <= x <= t.bbox[2] and t.bbox[1] <= y <= t.bbox[3] for t in tables)
    pieces = []
    # Reading regions retain prose before/between/after tables in page order.
    cursor = page.bbox[1]
    for number, table in enumerate(sorted(tables, key=lambda t:t.bbox[1]), 1):
        prose = page.filter(lambda obj: outside_tables(obj) and
                            (obj.get('object_type') != 'char' or cursor <= obj['top'] < table.bbox[3]))
        pieces.append(prose.extract_text() or '')
        rows = table.extract()
        pieces.append(f'[表格{number}：逐行读取，空单元格未补写]\n' + '\n'.join(
            ' | '.join((cell or '').replace('\n', ' / ') for cell in row) for row in rows))
        cursor = max(cursor, table.bbox[3])
    tail = page.filter(lambda obj: outside_tables(obj) and
                       (obj.get('object_type') != 'char' or obj['top'] >= cursor))
    pieces.append(tail.extract_text() or '')
    return {'text':'\n\n'.join(p for p in pieces if p.strip()),
            'extraction_method':'pdf_tables', 'table_count':len(tables)}


def pdf_column_layout(page):
    """Detect stable newspaper gutters, adapting to full-width panel separators."""
    chars = [c for c in page.chars if c.get('text', '').strip()]
    if len(chars) < 600 or page.height / page.width < 1.0:
        return None
    sizes = sorted(c['size'] for c in chars)
    body_size = sizes[len(sizes)//2]

    def gutters_for(region):
        occupancy = [0] * int(page.width)
        for c in region:
            if c['size'] > body_size * 1.05:
                continue
            for x in range(max(0, int(c['x0'])), min(len(occupancy), int(c['x1']) + 1)):
                occupancy[x] += 1
        peak = sorted(occupancy)[int(len(occupancy) * .9)]
        if peak < 8:
            return []
        gutters, start = [], None
        for x, count in enumerate(occupancy):
            left_peak = max(occupancy[max(0,x-40):max(0,x-5)] or [0])
            right_peak = max(occupancy[x+5:x+40] or [0])
            threshold = max(2, min(left_peak, right_peak)*.28)
            if count <= threshold and min(left_peak, right_peak) >= 8:
                if start is None:
                    start = x
            elif start is not None:
                if (4 <= x-start <= page.width*.15 and start > page.width*.1 and x < page.width*.9
                        and max(occupancy[max(0,start-30):start] or [0]) >= 8
                        and max(occupancy[x:x+30] or [0]) >= 8):
                    gutters.append((start+x)/2)
                start = None
        return gutters

    global_gutters = gutters_for(chars)
    if not 1 <= len(global_gutters) <= 7:
        return None
    separators = sorted(r['top'] for r in [*page.rects, *page.lines]
                        if r.get('width', 0) > page.width*.8 and r.get('height', 0) < 2
                        and page.height*.25 < r['top'] < page.height*.95)
    cuts = []
    for y in separators:
        if not cuts or y-cuts[-1] > 8:
            cuts.append(y)
    bands = [page.bbox[1], *cuts, page.bbox[3]]
    regions = []
    for top, bottom in zip(bands, bands[1:]):
        region_chars = [c for c in chars if top <= c["top"] < bottom]
        gutters = gutters_for(region_chars)
        if not 1 <= len(gutters) <= 7:
            gutters = global_gutters
        regions.append({"top":top, "bottom":bottom, "boundaries":[page.bbox[0], *gutters, page.bbox[2]]})
    return {"column_boundaries":[page.bbox[0], *global_gutters, page.bbox[2]], "column_regions":regions, "body_size":body_size}

def pdf_column_text(page, layout=None):
    layout = pdf_column_layout(page) if layout is None else layout
    if not layout:
        return None
    headline_size = layout['body_size'] * 1.1
    def is_heading(obj):
        return obj.get('size', 0) >= headline_size or obj.get('top', 0) < page.height*.09
    headings = page.filter(lambda obj: obj.get('object_type') != 'char' or is_heading(obj))
    parts = ["[页面标题及图注文字，彼此不代表同一报道]\n" + (headings.extract_text() or '')]
    for band_index, region in enumerate(layout['column_regions'], 1):
        top, bottom = region['top'], region['bottom']
        for index, (left, right) in enumerate(zip(region['boundaries'], region['boundaries'][1:]), 1):
            column = page.filter(lambda obj: obj.get('object_type') != 'char' or
                                 (not is_heading(obj) and top <= obj['top'] < bottom and
                                  left <= (obj['x0']+obj['x1'])/2 < right))
            text = column.extract_text() or ''
            if text.strip():
                parts.append(f"[区域{band_index}第{index}栏：自上而下，不同报道不代表同一事件]\n{text}")
    return {'text':'\n\n'.join(parts), 'extraction_method':'pdf_columns',
            'column_boundaries':layout['column_boundaries'], 'column_regions':layout['column_regions']}


def needs_pdf_ocr(page, text: str) -> bool:
    """Detect scans, sparse page-number layers, and text rendered as vector paths."""
    # Some mathematical PDF fonts expose glyphs with empty Unicode mappings.
    if sum(not c.get('text', '') for c in getattr(page, 'chars', []) if 'math' in c.get('fontname', '').lower()) >= 3:
        return True
    if not text.strip():
        return bool(page.images or page.curves or page.lines or page.rects)
    if re.search(r'\(cid:\d+\)', text) or text.count('\ufffd') >= 3:
        return True
    readable_chars = sum(character.isalnum() for character in text)
    if readable_chars > 60:
        return False
    area = max(1, float(page.width) * float(page.height))
    return any(max(0, float(image['x1']) - float(image['x0'])) *
               max(0, float(image['bottom']) - float(image['top'])) >= area * .35
               for image in page.images)


def formula_page_image(path, page_index):
    """Bounded page image for source-linked formula transcription, not OCR truth."""
    import base64
    from io import BytesIO
    import pypdfium2 as pdfium
    from app.rag.pdf_runtime import pdfium_lock
    with pdfium_lock, pdfium.PdfDocument(str(path)) as pdf:
        page=pdf[page_index]
        try:
            width,height=page.get_size()
            bitmap=page.render(scale=min(2,2200/max(width,height)))
            try:
                buffer=BytesIO()
                bitmap.to_pil().convert('RGB').save(buffer,format='PNG')
                if buffer.tell()>2_000_000: raise ValueError('公式页面图像超过识别预算')
                return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')
            finally:bitmap.close()
        finally:page.close()


def _parse_non_pdf(path: Path, audit=None) -> list[dict]:
    path=readable_path(path)
    suffix = path.suffix.lower()
    pages: list[dict] = []
    if audit is not None:
        audit['pages'] = pages
    def log_reading(reading, page_no=None):
        if audit is not None and reading.get('ocr_execution'):
            audit['execution_record'].append({'step': 'visual_transcription', 'page_no': page_no,
                                              'status': 'completed', 'ocr_execution': reading['ocr_execution']})
    def inspect_image(image, page_no):
        if audit is not None:
            from app.rag.document_quality import raster_quality_row, summarize_quality
            from app.rag.document_assessment import load_rules
            rules = load_rules()['quality']
            rows = [*audit['quality_report'].get('pages', []), raster_quality_row(image, page_no, rules)]
            audit['quality_report'] = summarize_quality(rows, audit['complexity_report']['source_hash'], rules)
    if path.name.lower().endswith(".ohr.json"):
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(rows, list) or not rows:
            raise ValueError("OHR原文必须是非空页面列表")
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) - {"text", "page_idx"}
                    or type(row.get("page_idx")) is not int or row["page_idx"] < 0
                    or not isinstance(row.get("text"), str) or row["page_idx"] in seen):
                raise ValueError("OHR原文页格式无效；问题答案文件不能建立知识索引")
            seen.add(row["page_idx"])
            pages.append({"page_no": row["page_idx"] + 1, "text": row["text"], "extraction_method": "ohr_ground_truth"})
    elif suffix in {".md", ".txt"}:
        from app.rag.text_parser import parse_text
        pages.append(parse_text(path))
        if audit is not None:
            audit['execution_record'].append({'step': 'text_decode', 'status': 'completed',
                                               'encoding': pages[-1]['text_encoding']})
    elif suffix == ".docx":
        from app.rag.word_markdown import parse_word
        pages.append(parse_word(path, on_ocr=log_reading))
    elif suffix in IMAGE_SUFFIXES:
        from app.rag.ocr import recognize_image, prepare_image
        from PIL import Image
        from io import BytesIO
        with Image.open(path) as image:
            count = getattr(image, 'n_frames', 1)
            if count > get_settings().ocr_max_pages:
                raise ValueError('图片帧数超过OCR预算，请拆分附件')
            render_bytes = 0
            for index in range(count):
                image.seek(index)
                if image.width * image.height > 16_000_000:
                    raise ValueError('图片超过1600万像素，请缩小后上传')
                stream = BytesIO()
                prepare_image(image).save(stream, format='PNG')
                render_bytes += stream.tell()
                if render_bytes > 50 * 1024 * 1024:
                    raise ValueError('图片渲染超过50MB预算，请拆分附件')
                inspect_image(image, index + 1)
                reading = recognize_image(stream.getvalue(), source_label=f'图片第{index + 1}帧')
                log_reading(reading, index + 1)
                pages.append({'page_no': index + 1, **reading})
    elif suffix in PRESENTATION_SUFFIXES:
        from app.rag.presentations import render_slides
        from app.rag.ocr import recognize_image
        if not get_settings().enable_ocr:
            raise ValueError('幻灯片需要OCR，但 ENABLE_OCR 已关闭')
        from time import perf_counter
        started = perf_counter()
        slides = render_slides(path)
        if audit is not None:
            audit['execution_record'].append({'step': 'powerpoint_render', 'status': 'completed',
                'slide_count': len(slides), 'duration_ms': round((perf_counter() - started) * 1000, 2),
                'read_only': True, 'macros_disabled': True})
        for index, content in enumerate(slides, 1):
            from PIL import Image
            from io import BytesIO
            with Image.open(BytesIO(content)) as image:
                inspect_image(image, index)
            reading = recognize_image(content, source_label=f'幻灯片第{index}页')
            log_reading(reading, index)
            pages.append({'page_no': index, **reading})
    else:
        raise ValueError(f"暂不支持的文档格式: {suffix}")
    return pages


class AssessedPages(list):
    """A compatible page list that also carries document-level audit reports."""
    def __init__(self, pages, assessment):
        super().__init__(pages)
        self.assessment = assessment


def pdf_mixed_text(page, prepared):
    tables = prepared['tables']
    def outside(obj):
        if obj.get('object_type') != 'char':
            return True
        x, y = (obj['x0']+obj['x1'])/2, (obj['top']+obj['bottom'])/2
        return not any(t.bbox[0] <= x <= t.bbox[2] and t.bbox[1] <= y <= t.bbox[3] for t in tables)
    prose = pdf_column_text(page.filter(outside), prepared['layout'])
    parts = [prose['text']]
    for i, table in enumerate(sorted(tables,key=lambda t:t.bbox[1]),1):
        parts.append(f'[独立表格{i}：保留行列，表格与分栏正文关系需核对原页]\n' + '\n'.join(
            ' | '.join((c or '').replace('\n',' / ') for c in row) for row in table.extract()))
    return {**prose, 'text':'\n\n'.join(parts), 'extraction_method':'pdf_mixed',
            'table_count':len(tables), 'parsing_warnings':['混合布局保留分栏正文与独立表格，未推断跨栏标题或表格与报道的归属关系。']}


from app.workflow.progress import observed

@observed('文档解析与质量检测')
def parse_document_with_assessment(path: Path) -> dict:
    from hashlib import sha256
    from time import perf_counter
    from app.rag.document_assessment import DocumentAssessmentError, load_rules, preflight_pdf, unavailable_report
    path = readable_path(path)
    rules = load_rules()
    source_hash = sha256(path.read_bytes()).hexdigest()
    started = perf_counter()
    result = {'pages': [], 'complexity_report': unavailable_report(path, rules),
              'quality_report': {'status':'unknown', 'score':None, 'pages':[],
                                 'reason':'质量检测尚未完成'}, 'execution_record': []}
    try:
        if path.suffix.lower() != '.pdf':
            result['complexity_report'] = unavailable_report(path,rules,status='not_applicable',
                reason='PDF图像与版面评分不适用于该格式，不记作零复杂度')
            result['quality_report'] = {'status':'not_applicable','score':None,'pages':[],
                                       'reason':'该格式未进行原页图像质量检测'}
            if path.suffix.lower() in IMAGE_SUFFIXES | PRESENTATION_SUFFIXES:
                result['quality_report'] = {'status':'unknown', 'score':None, 'pages':[], 'reason':'等待本地图像检测；OCR置信分数未提供'}
            result['pages'] = _parse_non_pdf(path, audit=result)
            result['execution_record'].append({'step':'document_parse','status':'completed',
                                               'method':result['pages'][0]['extraction_method'] if result['pages'] else 'none'})
            return result
        with pdfplumber.open(path) as pdf:
            count = len(pdf.pages)
            if count > get_settings().ocr_max_pages:
                result['complexity_report'] = unavailable_report(path,rules,status='blocked',page_count=count,
                    reason='PDF页数超过解析预算，请拆分文档')
                raise DocumentAssessmentError('PDF页数超过解析预算，请拆分文档',result)
            report, prepared = preflight_pdf(pdf, source_hash, rules)
            result['complexity_report'] = report
            result['execution_record'].append({'step':'document_preflight','status':report['status'],
                                               'duration_ms':round((perf_counter()-started)*1000,2),
                                               'assessed_page_count':report['assessed_page_count']})
            if report['unknown_pages']:
                raise DocumentAssessmentError('页面预检未完成，未执行不完整解析：'+str(report['unknown_pages']),result)
            from app.rag.document_quality import quality_preflight, finish_page_quality, quality_warnings, summarize_quality
            quality_rows = quality_preflight(path,pdf,prepared,rules)
            result['quality_report'] = summarize_quality(quality_rows,source_hash,rules['quality'])
            result['quality_report']['rule_fingerprint'] = rules['fingerprint']
            result['execution_record'].append({'step':'document_quality_precheck','status':result['quality_report']['status'],
                                               'note':'本地图像质量预检已完成；Qwen转写不提供实测OCR方向或行置信度'})
            for index, (page, item) in enumerate(zip(pdf.pages, prepared),1):
                plan = item['report']['strategy']
                tick = perf_counter()
                method = plan['method']
                if method == 'ocr':
                    from app.rag.ocr import recognize_page
                    try:
                        extracted = recognize_page(path,index-1)
                    except Exception as exc:
                        from app.rag.ocr import OCRQualityError
                        if isinstance(exc,OCRQualityError):
                            from app.rag.document_quality import check
                            quality_rows[index-1]['checks']['ocr'] = check('fail',exc.quality,
                                method='actual_ocr_rejection',phase='post_parse',reason=str(exc))
                            result['quality_report'] = summarize_quality(quality_rows,source_hash,rules['quality'])
                            result['quality_report']['rule_fingerprint'] = rules['fingerprint']
                        result['execution_record'].append({'step':'page_parse','page_no':index,
                            'method':method,'status':'failed','reason':f'{type(exc).__name__}: {exc}',
                            **({'ocr_execution': exc.quality['ocr_execution']} if isinstance(exc, OCRQualityError) and exc.quality.get('ocr_execution') else {}),
                            'duration_ms':round((perf_counter()-tick)*1000,2)})
                        raise DocumentAssessmentError(f'第{index}页OCR解析失败: {exc}',result) from exc
                    if index <= 8:
                        extracted['formula_image'] = formula_page_image(path,index-1)
                    else:
                        extracted.setdefault('parsing_warnings',[]).append('公式页面图像保留预算为前8页；后续页保留OCR文字，视觉公式提取未覆盖。')
                elif method == 'pdf_mixed':
                    extracted = pdf_mixed_text(page,item)
                elif method == 'pdf_tables':
                    extracted = pdf_table_text(page,item['tables'])
                elif method == 'pdf_columns':
                    extracted = pdf_column_text(page,item['layout'])
                else:
                    extracted = {'text':item['text'],'extraction_method':'pdf_text'}
                if method != 'ocr':
                    from app.rag.section_location import native_headings
                    extracted['headings'] = native_headings(page, extracted['text'])
                finish_page_quality(quality_rows[index-1],extracted,rules['quality'])
                result['quality_report'] = summarize_quality(quality_rows,source_hash,rules['quality'])
                result['quality_report']['rule_fingerprint'] = rules['fingerprint']
                warnings = list(dict.fromkeys([*extracted.get('parsing_warnings',[]), *plan['warnings'],
                                               *quality_warnings(quality_rows[index-1])]))
                if warnings:
                    extracted['parsing_warnings'] = warnings
                result['pages'].append({'page_no':index,**extracted})
                result['execution_record'].append({'step':'page_parse','page_no':index,'status':'completed',
                                                   'method':extracted['extraction_method'],
                                                    **({'ocr_execution': extracted['ocr_execution']} if extracted.get('ocr_execution') else {}),
                                                   'duration_ms':round((perf_counter()-tick)*1000,2)})
        return result
    except DocumentAssessmentError as exc:
        if not any(r['status']=='failed' for r in result['execution_record']):
            result['execution_record'].append({'step':'document_parse','status':'failed','reason':str(exc)})
        raise
    except Exception as exc:
        quality = getattr(exc, 'quality', {})
        result['execution_record'].append({'step':'document_parse','status':'failed','reason':str(exc),
            **({'ocr_execution': quality['ocr_execution']} if quality.get('ocr_execution') else {})})
        raise DocumentAssessmentError(f'文档评估或解析失败: {exc}',result) from exc


def parse_document(path: Path) -> list[dict]:
    result = parse_document_with_assessment(path)
    return AssessedPages(result['pages'], {k:v for k,v in result.items() if k!='pages'})
