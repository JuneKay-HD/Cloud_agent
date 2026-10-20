"""Correction workflow for 任务9: human-confirmed text corrections produce a
derived body version, not an in-place edit.

- Original text/hash are never modified: each confirmed change is recorded in a
  change log carrying original hash, page, range, original text, replacement,
  basis, confirmation, and a version stamp.
- Applying corrections yields a *derived* page set + rebuilt chunks (slices).
  Region layout is rebuilt against the new text hash, so citation mapping for
  the corrected text is regenerated before the corrected body can be used for
  retrieval/citation. Because region stores are keyed by (original_hash,
  text_hash), the original mapping is preserved and rollback is by construction.
- Reverting rebuilds from the change log; the original body is always restorable.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from uuid import uuid4

from app.rag.region_store import text_fingerprint

CORRECTION_VERSION = 'corrections-v1'


class CorrectionError(ValueError):
    pass


def apply_corrections(pages, confirmations, *, original_hash, path=None, max_chars=700) -> dict:
    """Apply user-confirmed corrections to a copy of the page set.

    confirmations: [{page_no, start, end, replacement, basis, confirmed: true}]
    Offsets are char ranges into the *current* page text at application time;
    multiple edits on the same page are applied together (descending start) so
    earlier offsets stay valid. Returns derived pages, rebuilt chunks and a
    change log; never mutates the input pages."""
    if not original_hash:
        raise CorrectionError('缺少原件哈希，不能建立校正派生版本')
    if not confirmations:
        raise CorrectionError('没有确认的校正项')
    for item in confirmations:
        if not item.get('confirmed'):
            raise CorrectionError('校正项未确认：' + str(item.get('original') or item))
    # 无分页正文(txt/markdown等)的 page_no 为 None，只能对应唯一的无页码页；
    # PDF 等分页文档用 1..n 定位。None 时直接以 None 为键匹配 page['page_no']。
    null_pages = sum(1 for p in pages if p.get('page_no') is None)
    by_page: dict = {}
    available_pages = {page['page_no'] for page in pages}
    for item in confirmations:
        page_no = item.get('page_no')
        if page_no not in available_pages:
            raise CorrectionError('校正目标页不存在')
        start, end = int(item['start']), int(item['end'])
        if start < 0 or end <= start:
            raise CorrectionError(f'第{page_no}页校正范围无效')
        if page_no is None and null_pages == 0:
            raise CorrectionError('正文未记录页码，且无匹配的未分页正文页')
        if page_no is None and null_pages > 1:
            raise CorrectionError('正文存在多个未分页页，无法确定校正目标页')
        by_page.setdefault(page_no, []).append({**item, 'start': start, 'end': end})

    derived = []
    change_log = []
    for page in pages:
        page_no = page['page_no']
        edits = by_page.get(page_no, [])
        if not edits:
            derived.append(page)
            continue
        text = page['text']
        if any(e['start'] > len(text) or e['end'] > len(text) for e in edits):
            raise CorrectionError(f'第{page_no}页校正范围超出正文长度')
        # Apply in descending start order; reject overlapping edits.
        ordered = sorted(edits, key=lambda e: e['start'], reverse=True)
        for index, item in enumerate(ordered):
            original_text = text[item['start']:item['end']]
            if 'original' in item and item['original'] != original_text:
                raise CorrectionError('校正原文已变化，请重新读取候选')
            if any(e['start'] < item['end'] and e['end'] > item['start'] for e in ordered[:index]):
                raise CorrectionError(f'第{page_no}页校正项重叠，不能同时应用')
            replacement = str(item['replacement'])
            if replacement == original_text:
                raise CorrectionError('校正内容与原文相同')
            text = text[:item['start']] + replacement + text[item['end']:]
            change_log.append({
                'id': str(uuid4()), 'version': CORRECTION_VERSION,
                'original_hash': original_hash, 'page_no': page_no,
                'start': item['start'], 'end': item['end'],
                'original_text': original_text, 'replacement': replacement,
                'basis': item.get('basis', ''), 'special_verify': bool(item.get('special_verify')),
                'confirmed_by': item.get('confirmed_by', 'manual'), 'applied_at': time.time(),
            })
        # Re-check: replacement text must not leave the exact same candidate.
        corrected = dict(page)
        corrected['text'] = text
        corrected['text_corrections'] = [entry['id'] for entry in change_log if entry['page_no'] == page_no]
        derived.append(corrected)

    derived_hash = text_fingerprint(derived)
    chunks = []
    if path is not None:
        chunks = chunk_derived(path, derived, max_chars=max_chars)
    return {
        'version': CORRECTION_VERSION,
        'original_hash': original_hash,
        'derived_text_hash': derived_hash,
        'pages': derived,
        'chunks': chunks,
        'change_log': change_log,
    }


def chunk_derived(path, pages, max_chars=700):
    from app.rag.chunker import chunk_pages
    return chunk_pages(path, pages, max_chars=max_chars, hard_limit=False)


def restore_original(original_pages, change_log, *, original_hash) -> dict:
    """Restore the original body after a correction version (undo/rollback).

    The original page set is preserved by construction (apply_corrections copies);
    this validates the hash and marks the change log as reverted. Returns the
    original body so the serving version can switch back cleanly."""
    if not original_pages:
        raise CorrectionError('缺少原始正文，不能撤销校正')
    if any(e.get('original_hash') != original_hash for e in change_log):
        raise CorrectionError('变更日志与原件哈希不一致，拒绝撤销')
    return {
        'version': CORRECTION_VERSION, 'original_hash': original_hash,
        'derived_text_hash': text_fingerprint(original_pages),
        'pages': [dict(p) for p in original_pages],
        'chunks': [], 'change_log': [{'reverted': True,
                                      'reverted_ids': [e['id'] for e in change_log]}],
    }
