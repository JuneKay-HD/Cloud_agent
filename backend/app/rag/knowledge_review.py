"""Versioned original-file measurements beside, not inside, the serving index."""
import hashlib
import json
from pathlib import Path
import threading
import time

from app.config import get_settings
from app.rag.document_assessment import load_rules, preflight_pdf
from app.rag.document_quality import quality_preflight, summarize_quality
from app.rag.region_store import text_fingerprint
from app.rag.semantic_index import atomic_json

REVIEW_VERSION = 'original-document-review-v2'
_LOCKS = [threading.Lock() for _ in range(32)]


def identity(source):
    return {'version': REVIEW_VERSION, 'source_hash': source.get('source_hash'),
            'original_hash': source.get('original_sha256') or
                (source.get('original_pdf') or {}).get('sha256') or source.get('source_hash'),
            'text_hash': text_fingerprint(source.get('pages', [])),
            'rule_fingerprint': load_rules()['fingerprint']}


def cache_path(doc_id, source):
    key = hashlib.sha256(json.dumps([doc_id, identity(source)], sort_keys=True).encode()).hexdigest()
    return get_settings().indexes_path / 'document_analysis' / (key + '.json')


def cached_review(doc_id, source):
    try:
        data = json.loads(cache_path(doc_id, source).read_text(encoding='utf-8'))
        return data if data.get('identity') == identity(source) else None
    except (OSError, ValueError):
        return None


def recommendations(complexity, quality):
    rows = []
    by_page = {p['page_no']: p for p in quality.get('pages', [])}
    for page in complexity.get('pages', []):
        steps = []
        checks = by_page.get(page['page_no'], {}).get('checks', {})
        for name, item in checks.items():
            if item['status'] not in {'fail', 'suspect'}:
                continue
            if name == 'orientation':
                steps.append(f"核对后将处理副本逆时针旋转 {item['value']}°，再解析")
            elif name == 'skew':
                steps.append('对处理副本纠偏后重新检测文字基线')
            elif name == 'illustrations':
                steps.append('替换低分辨率或模糊插图；放大图片不能恢复丢失细节')
            elif name in {'blur', 'contrast'}:
                steps.append('重新扫描或提高原图质量后再识别')
            elif name == 'ocr':
                steps.append('对低置信文字逐行核对原件')
        method = page.get('strategy', {}).get('method', 'blocked')
        labels = {'ocr': 'OCR转写并保留原页复核', 'pdf_text': '优先提取原生文字层',
                  'pdf_columns': '按栏与阅读顺序提取', 'pdf_tables': '按表格行列提取',
                  'pdf_mixed': '按文字、表格和多栏区域分别提取', 'blocked': '补充可读原件后重试'}
        steps.append(labels.get(method, method))
        if page.get('features', {}).get('math_character_count'):
            steps.append('保留公式图像并核对符号、上下标及变量')
        rows.append({'page_no': page['page_no'], 'method': method, 'steps': steps,
                     'requires_review': any(c['status'] in {'fail', 'suspect', 'unknown'} for c in checks.values())})
    return rows


def analyze_original(doc_id, source, path, *, force=False):
    target = cache_path(doc_id, source)
    lock = _LOCKS[int(target.stem[:8], 16) % len(_LOCKS)]
    with lock:
        cached = cached_review(doc_id, source)
        if cached and not force:
            return cached
        rules = load_rules()
        started = time.perf_counter()
        ident = identity(source)
        if hashlib.sha256(path.read_bytes()).hexdigest() != ident['original_hash']:
            raise ValueError('分析原件哈希不一致')
        if path.suffix.lower() == '.pdf':
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                complexity, prepared = preflight_pdf(pdf, ident['original_hash'], rules)
                rows = quality_preflight(path, pdf, prepared, rules)
            quality = summarize_quality(rows, ident['original_hash'], rules['quality'])
        else:
            from PIL import Image
            from app.rag.document_quality import raster_quality_row
            with Image.open(path) as image:
                row = raster_quality_row(image, 1, rules['quality'])
            complexity = {'status': 'partial', 'score': None, 'level': 'unknown', 'pages': [],
                          'reason': '图片完成质量测量；PDF对象复杂度规则不适用于无对象文字层的图片'}
            quality = summarize_quality([row], ident['original_hash'], rules['quality'])
        complexity['model'] = {'kind': 'explainable_rules', 'calibration': '未以业务标注集校准',
            'weights': rules['weights'], 'levels': rules['levels'],
            'formula': '页面=35A+25L+20T+20F；文档=0.7×均值+0.3×P90（仅完整覆盖时）'}
        quality['rule_fingerprint'] = rules['fingerprint']
        # The probe does not run the cloud transcriber; do not claim it did.
        quality['limitations'] = [s for s in quality['limitations'] if not s.startswith('Qwen返回')]
        data = {'identity': ident, 'assessment_target': 'original_pdf' if source.get('original_pdf') else 'original_file',
                'original_filename': path.name, 'analyzed_at': time.time(),
                'reports': {'complexity_report': complexity, 'quality_report': quality,
                    'execution_record': [{'step': 'original_document_quality_review',
                        'status': 'completed', 'source_hash': ident['source_hash'],
                        'original_hash': ident['original_hash'], 'cloud_calls': 0,
                        'duration_ms': round((time.perf_counter()-started)*1000, 2)}]},
                'recommendations': recommendations(complexity, quality)}
        if hashlib.sha256(path.read_bytes()).hexdigest() != ident['original_hash']:
            raise ValueError('分析期间原件发生变化，请重试')
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(target, data)
        return data


def candidate_id(item):
    return hashlib.sha256(json.dumps([item['page_no'], item['start'], item['end'],
        item['original'], item['suggested']], ensure_ascii=False).encode()).hexdigest()[:24]
