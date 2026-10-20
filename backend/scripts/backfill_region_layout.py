"""Backfill region side tables for a persisted knowledge index.

用途（与新增脚本的约束一致）：为“点击引用定位”提供可复用的区域侧表，而不是
在每次请求时重查整页。脚本只读取当前 `knowledge_index.json` 中已存在、且带有
可验证原始 PDF（`original_pdf`）的来源，为缺失的（原件哈希, 正文文本版本）组合
补建侧表。

约束与保证：
- 幂等：按 `original_hash` + `text_hash` 定位；同一组合已存在且结构校验通过的侧
  表会被跳过，重跑不会覆盖旧侧表（文本修复会自然产生新的 text_hash，旧侧表保留）。
- 只读对齐：不修改 knowledge_index.json，不改写文档正文；区域构建失败按来源记
  录在失败清单里，不会静默把局部结果当完整成功。
- 先验证再回填：可先用 `--sample news__07_04`（或 `--sample-doc-id`）在单个样本
  上构建并核对，通过后再对全量来源执行 `--all`。

用法（在 backend 目录下，PYTHONIOENCODING=utf-8）：
    .venv/Scripts/python.exe scripts/backfill_region_layout.py --sample news__07_04
    .venv/Scripts/python.exe scripts/backfill_region_layout.py --all
    .venv/Scripts/python.exe scripts/backfill_region_layout.py --doc-id <doc_id> ...
"""
import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.rag import region_native, region_store


def _sha256(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def _resolve_original(source: dict, settings) -> tuple:
    """Return (pdf_path, sha256) for a source with a verifiable PDF original.

    Mirrors api.documents._knowledge_original / region_build.knowledge_original_path
    without HTTP exceptions: OHR sources map to ../pdfs/<relative_path> with hash
    verification; native PDFs verify against source_hash.
    """
    root = settings.documents_path
    original = source.get('original_pdf')
    try:
        if original:
            path = (root.parent / 'pdfs' / original['relative_path']).resolve()
            if not path.is_file() or path.suffix.lower() != '.pdf':
                return None, '缺失或非PDF原件'
            return path, (_sha256(path) if _sha256(path) == original['sha256'] else None)
        path = (root / source['filename']).resolve()
        if path.suffix.lower() != '.pdf' or not path.is_file():
            return None, '非PDF来源，无区域几何'
        return path, (_sha256(path) if _sha256(path) == source['source_hash'] else None)
    except OSError as exc:
        return None, f'原件读取失败：{exc}'


def _existing_store(settings, original_hash, text_hash):
    path = region_store.store_path(region_build_dir(settings), original_hash, text_hash)
    if not path.is_file():
        return None
    store = region_store.load_store(region_build_dir(settings), original_hash, text_hash)
    return store


def region_build_dir(settings) -> Path:
    return settings.indexes_path / 'region_layout'


def build_for_source(source_key, source, chunks, settings, *, force=False):
    """Build one source's region store; idempotent and failure-recorded."""
    path, original_hash = _resolve_original(source, settings)
    if path is None:
        return {'source': source_key, 'status': 'skipped', 'reason': original_hash}
    pages = source.get('pages', [])
    if not pages:
        return {'source': source_key, 'status': 'skipped', 'reason': '无页面文本'}
    text_hash = region_store.text_fingerprint(pages)
    existing = _existing_store(settings, original_hash, text_hash)
    if existing is not None and not force:
        return {'source': source_key, 'status': 'exists', 'original_hash': original_hash,
                'text_hash': text_hash, 'pages': len(existing.get('pages', [])),
                'exact_chunks': sum(1 for c in existing.get('chunks', {}).values()
                                    if c.get('level') == 'exact')}
    try:
        store = region_native.build_document_store(path, pages, chunks, original_hash,
                                                   cache_dir=settings.indexes_path / 'source_layout')
        target = region_store.save_store(region_build_dir(settings), store)
    except Exception as exc:  # noqa: BLE001 — 失败按来源记录，不中断整个回填
        return {'source': source_key, 'status': 'failed', 'original_hash': original_hash,
                'text_hash': text_hash, 'reason': f'{type(exc).__name__}: {exc}'}
    return {'source': source_key, 'status': 'built', 'original_hash': original_hash,
            'text_hash': text_hash, 'path': str(target),
            'pages': len(store.get('pages', [])),
            'exact_chunks': sum(1 for c in store.get('chunks', {}).values()
                                if c.get('level') == 'exact')}


def _resolve_keys(doc, sources):
    """Resolve a source key from its hash id OR its ohr filename fragment.

    OHR source keys are content hashes; `news__07_04` refers to the source
    whose filename is `news__07_04.ohr.json`. Only unique matches resolve.
    """
    if doc in sources:
        return {doc}
    matches = [k for k, s in sources.items() if doc in s.get('filename', '')]
    if len(matches) == 1:
        return {matches[0]}
    return set()


def _load_index():
    """Persisted serving index (no rebuild); the script never reparses the corpus."""
    from app.rag.indexer import read_serving_index
    data = read_serving_index()
    if data.get('mode') == 'unavailable':
        raise RuntimeError('knowledge_index.json 缺失或版本过期，请先运行索引构建任务再回填区域侧表')
    return data


def _summarize(results):
    by_status = {}
    failures = []
    for row in results:
        by_status[row['status']] = by_status.get(row['status'], 0) + 1
        if row['status'] == 'failed':
            failures.append(row)
    return by_status, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='为索引中全部带PDF原件的来源回填区域侧表')
    parser.add_argument('--sample', metavar='DOC_ID', help='只为指定来源（如 news__07_04）构建并校验')
    parser.add_argument('--doc-id', action='append', metavar='DOC_ID', default=[], help='为指定来源（可多次）构建')
    parser.add_argument('--force', action='store_true', help='已存在且校验通过也重建（一般不使用）')
    args = parser.parse_args()

    if not (args.all or args.sample or args.doc_id):
        parser.error('请指定 --all 或 --sample/--doc-id 来源')
    settings = get_settings()
    data = _load_index()
    sources, chunks = data.get('sources', {}), data.get('chunks', [])
    chunks_by_doc = {}
    for chunk in chunks:
        chunks_by_doc.setdefault(chunk.get('doc_id'), []).append(chunk)

    wanted = set(sources)
    if args.sample:
        wanted = _resolve_keys(args.sample, sources)
        if not wanted:
            raise SystemExit(f'未知来源：{args.sample}（可用样本示例：news__07_04）')
    if args.doc_id:
        resolved = set()
        for doc in args.doc_id:
            resolved |= _resolve_keys(doc, sources)
        if not resolved:
            raise SystemExit(f'未知来源：{args.doc_id}')
        wanted = wanted & resolved if args.all else resolved

    results = []
    for key in sorted(wanted):
        results.append(build_for_source(key, sources[key], chunks_by_doc.get(key, []), settings,
                                        force=args.force))
    by_status, failures = _summarize(results)
    print('区域侧表回填结果：')
    for status, count in sorted(by_status.items()):
        print(f'  {status}: {count}')
    if failures:
        print('失败记录（未静默替换）：')
        for row in failures:
            print(f'  {row["source"]}: {row["reason"]}')
    # 样本路径同时验证三处预定义字符区间可被精确映射（page 1 的 [0,120)/[300,420)/[600,720)）。
    if args.sample and results and results[0]['status'] in {'built', 'exists'}:
        sample_key = next(iter(wanted))
        verify_sample_span(sources[sample_key], results[0], settings)
    sys.exit(1 if failures else 0)


def verify_sample_span(source, built, settings):
    """对 news__07_04 的第一页验证三个字符区间能解析出非 none 的区域。"""
    store = region_store.load_store(region_build_dir(settings), built.get('original_hash'), built.get('text_hash'))
    if store is None:
        print('  样本校验：无法重新加载刚构建的侧表，区域回填失败。')
        sys.exit(1)
    page = region_store.page_record(store, 1)
    if page is None:
        print('  样本校验：news__07_04 第1页无区域记录。')
        sys.exit(1)
    text = source.get('pages', [])[0].get('text', '')
    ok = True
    for start, end in [(0, 120), (300, 420), (600, 720)]:
        resolved = region_store.regions_for_span(page, text, start, end)
        level = resolved.get('level', 'none')
        print(f'  区间[{start},{end}) -> level={level} ({resolved.get("level_note", "")})')
        if level == 'none':
            ok = False
    print('  样本校验：' + ('三个区间均可定位。' if ok else '存在无法定位的区间，需核对页面对齐。'))
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
