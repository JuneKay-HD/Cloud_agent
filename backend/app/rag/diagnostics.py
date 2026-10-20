"""Read-only, page-aware diagnostics for an already built knowledge index.

Transcription limitations are not parser exceptions. Image quality is separate
from recognition correctness; this module never upgrades either measurement.
"""
import re


def display_name(filename):
    return re.sub(r'^upload_[0-9a-f]{32}_', '', filename or '文档')


def knowledge_issues(data):
    issues = [{**error, 'filename': display_name(error.get('filename', '文档')),
               'severity': 'error', 'code': 'parse_failed', 'usable': False}
              for error in data.get('errors', [])]
    for doc_id, source in data.get('sources', {}).items():
        warnings = {}
        review_pages = set()
        for page in source.get('pages', []):
            messages = list(page.get('parsing_warnings') or [])
            unverified = page.get('ocr_validation') == 'unverified_visual_transcription'
            # Older snapshots may retain the status but not the warning string.
            if unverified:
                review_pages.add(page.get('page_no'))
                if not messages:
                    messages.append('视觉转写结果尚未独立核验。')
            for message in messages:
                warnings.setdefault(message, set()).add(page.get('page_no'))
        if not warnings:
            continue
        pages = sorted({p for values in warnings.values() for p in values if type(p) is int})
        excluded_pages = [p['page_no'] for p in source.get('pages', [])
                          if p.get('ocr_uncertain_ranges')]
        # Only the generic provider limitation is informational. Actual image
        # defects, empty transcriptions and quarantined text remain warnings.
        quality = source.get('quality_report')
        quality_attention = any(check.get('status') in {'suspect', 'fail'}
                                for row in (quality or {}).get('pages', [])
                                for check in row.get('checks', {}).values())
        generic_only = bool(review_pages) and not excluded_pages and not quality_attention and all(
            'Qwen视觉转写为待核对文本' in message or message == '视觉转写结果尚未独立核验。'
            for message in warnings)
        issue = {'doc_id': doc_id, 'filename': display_name(source['filename']),
                 'severity': 'notice' if generic_only else 'warning',
                 'code': 'visual_transcription_unverified' if generic_only else 'document_review',
                 'usable': True, 'page_nos': pages, 'excluded_pages': excluded_pages,
                 'reason': ('文字转写已完成，文档可检索；转写内容尚未独立核验。'
                            if generic_only else '文档已入库，部分内容需要核对；请查看具体页码和说明。'),
                 'details': [{'reason': message, 'page_nos': sorted(p for p in values if type(p) is int)}
                             for message, values in warnings.items()]}
        if quality:
            issue['quality_report'] = quality
        if source['filename'].lower().endswith('.pdf') or source.get('original_pdf'):
            issue['file_url'] = f'/api/documents/{doc_id}/file'
        issues.append(issue)
    return issues
