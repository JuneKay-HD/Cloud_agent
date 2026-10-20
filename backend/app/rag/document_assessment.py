"""PDF-object preflight, reproducible heuristic cost scores and provenance."""
import hashlib
import math
import re
import time
from pathlib import Path

import yaml

from app.config import PROJECT_ROOT, get_settings
from app.rag.document_strategy import page_strategy


class DocumentAssessmentError(ValueError):
    def __init__(self, message: str, assessment: dict):
        super().__init__(message)
        self.assessment = assessment


def load_rules() -> dict:
    path = get_settings().config_path / 'document_complexity.yaml'
    if not path.exists():
        path = PROJECT_ROOT / 'config/document_complexity.yaml'
    raw = path.read_bytes()
    rules = yaml.safe_load(raw)
    weights = rules['weights']
    if set(weights) != {'A', 'L', 'T', 'F'} or any(
        not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in weights.values()
    ) or sum(weights.values()) != 100:
        raise ValueError('复杂度权重必须为A/L/T/F的非负有限数，合计100')
    if not 0 <= rules['levels']['medium'] < rules['levels']['high'] <= 100:
        raise ValueError('复杂度档位阈值无效')
    if not 0 < rules['preflight_seconds'] <= 120:
        raise ValueError('预检时间预算无效')
    q = rules['quality']
    if not (0 < q['render_scale'] <= 4 and 100 <= q['max_dimension'] <= 3000
            and 0 < q.get('seconds',30) <= 120 and 0 <= q['orientation_max_pages'] <= 8
            and 1 <= q['orientation_max_regions'] <= 2 and 0 < q['orientation_margin'] < 1
            and q['orientation_min_characters'] >= 8):
        raise ValueError('质量检测预算或方向阈值无效')
    for prefix in ('blur','contrast'):
        if not 0 <= q[prefix+'_fail'] < q[prefix+'_suspect']:
            raise ValueError('质量阈值顺序无效：'+prefix)
    for pair in (('illustration_resolution_fail','illustration_resolution_suspect'),
                 ('illustration_blur_fail','illustration_blur_suspect')):
        if not 0 < q.get(pair[0],0) < q.get(pair[1],1):
            raise ValueError('插图阈值顺序无效：'+pair[1])
    q.setdefault('illustration_max_regions', 4)
    if not 0 < q['skew_suspect_degrees'] < q['skew_fail_degrees'] <= 45:
        raise ValueError('倾斜角度阈值无效')
    rules['fingerprint'] = hashlib.sha256(raw).hexdigest()
    return rules


def level(score, rules):
    if score is None:
        return 'unknown'
    return 'high' if score >= rules['levels']['high'] else 'medium' if score >= rules['levels']['medium'] else 'low'


def assess_page(page, page_no: int, rules: dict) -> dict:
    from app.rag.parsers import needs_pdf_ocr, pdf_column_layout
    try:
        text = page.extract_text(layout=True) or ''
        chars = page.chars
        math_chars = [c for c in chars if 'math' in c.get('fontname', '').lower()]
        missing_math = sum(not c.get('text', '') for c in math_chars)
        area = max(1, float(page.width) * float(page.height))
        images = page.images
        coverage = min(1, sum(max(0, float(i['x1'])-float(i['x0'])) *
                             max(0, float(i['bottom'])-float(i['top'])) for i in images) / area)
        layout = pdf_column_layout(page)
        tables = [t for t in page.find_tables() if len(t.rows) >= 2 and len(t.columns) >= 2]
        ocr = needs_pdf_ocr(page, text)
        features = {
            'readable_character_count': sum(c.isalnum() for c in text),
            'image_coverage': round(coverage, 4),
            'bad_encoding_count': len(re.findall(r'\(cid:\d+\)', text)) + text.count('\ufffd'),
            'math_mapping_missing': missing_math >= 3,
            'math_character_count': len(math_chars), 'missing_math_character_count': missing_math,
            'column_count': len(layout['column_boundaries'])-1 if layout else 1,
            'table_count': len(tables), 'ocr_required': ocr,
            'blank_candidate': not text.strip() and not (images or page.curves or page.lines or page.rects),
            'rotation_metadata': int(getattr(page, 'rotation', 0) or 0),
        }
        dimensions = {'A': 1.0 if ocr else 0.0,
                      'L': min(1.0, (features['column_count']-1)/3),
                      'T': min(1.0, sum(len(t.rows)*len(t.columns) for t in tables)/40),
                      'F': 1.0 if missing_math >= 3 else min(.5, len(math_chars)/max(1,len(chars)))}
        score = round(sum(rules['weights'][k]*v for k,v in dimensions.items()), 2)
        report = {'page_no': page_no, 'status': 'completed', 'features': features,
                  'dimensions': dimensions, 'score': score, 'level': level(score,rules),
                  'strategy': page_strategy(features)}
        return {'report': report, 'text': text, 'layout': layout, 'tables': tables}
    except Exception as exc:
        return {'report': {'page_no': page_no, 'status': 'unknown', 'score': None, 'level': 'unknown',
                           'features': {}, 'dimensions': {k:None for k in 'ALTF'},
                           'reason': f'{type(exc).__name__}: {exc}',
                           'strategy': page_strategy({}, unknown=True)}}


def aggregate_pages(rows: list[dict], page_count: int, source_hash: str, rules: dict) -> dict:
    known = [p for p in rows if p['score'] is not None]
    complete = len(known) == page_count and page_count > 0
    values = sorted(p['score'] for p in known)
    mean = sum(values)/len(values) if values else None
    p90 = values[max(0, math.ceil(.9*len(values))-1)] if values else None
    score = round(.7*mean+.3*p90, 2) if complete else None
    maximum = max(values) if values else None
    return {'status': 'completed' if complete else 'partial', 'score': score, 'level': level(score,rules),
            'source_hash': source_hash, 'format': 'pdf', 'rule_version': rules['version'],
            'rule_fingerprint': rules['fingerprint'], 'page_count': page_count,
            'assessed_page_count': len(known), 'mean_score': round(mean,2) if mean is not None else None,
            'p90_score': p90, 'max_score': maximum,
            'highest_score_pages': [p['page_no'] for p in known if p['score']==maximum and maximum>0],
            'unknown_pages': [p['page_no'] for p in rows if p['score'] is None],
            'ocr_page_count': sum(p['features'].get('ocr_required',False) for p in rows),
            'pages': rows,
            'limitations': ['规则分数用于预估解析难度，不是文档质量或事实准确率。',
                           '扫描图像内部布局与公式未在无OCR预检中识别；评分基于可观察PDF对象。']}


def preflight_pdf(pdf, source_hash: str, rules: dict) -> tuple[dict, list[dict]]:
    started = time.perf_counter()
    prepared = []
    for number, page in enumerate(pdf.pages, 1):
        if time.perf_counter()-started > rules['preflight_seconds']:
            prepared.append({'report': {'page_no': number, 'status': 'unknown', 'score': None,
                             'level': 'unknown', 'features': {}, 'dimensions': {k:None for k in 'ALTF'},
                             'reason': '文档预检时间预算已用尽', 'strategy': page_strategy({}, unknown=True)}})
        else:
            prepared.append(assess_page(page, number, rules))
    report = aggregate_pages([p['report'] for p in prepared], len(pdf.pages), source_hash, rules)
    return report, prepared


def unavailable_report(path: Path, rules: dict, *, status='unknown', page_count=None, reason='') -> dict:
    return {'status': status, 'score': None, 'level': 'unknown', 'format': path.suffix.lstrip('.'),
            'source_hash': hashlib.sha256(path.read_bytes()).hexdigest(), 'rule_version': rules['version'],
            'rule_fingerprint': rules['fingerprint'], 'page_count': page_count,
            'assessed_page_count': 0, 'unknown_pages': [], 'pages': [], 'reason': reason}
