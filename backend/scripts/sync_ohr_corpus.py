"""Preflight and sync all canonical OHR page text into the active corpus.

PDFs remain original files; QA and OCR/noise variants stay outside documents.
No credentials, business database, or conversation history are changed.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.local_paths import readable_path


def sha(path):
    digest = hashlib.sha256()
    with readable_path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sync_corpus(root):
    """Validate the entire source and all conflicts before adding any document."""
    import pypdfium2 as pdfium
    from app.rag.pdf_runtime import pdfium_lock
    root = Path(root).resolve()
    gt, pdfs, target = root/'retrieval/gt', root/'pdfs', root/'documents'
    inputs = {p.relative_to(gt).with_suffix(''): p for p in gt.rglob('*.json')}
    originals = {p.relative_to(pdfs).with_suffix(''): p for p in pdfs.rglob('*.pdf')}
    if not inputs or inputs.keys() != originals.keys():
        raise ValueError('GT/PDF document coverage differs')
    rows, digests = [], defaultdict(list)
    filenames = set()
    for identity, source in sorted(inputs.items()):
        if len(identity.parts) != 2:
            raise ValueError('Expected category/document structure')
        filename = identity.parts[0]+'__'+identity.name+'.ohr.json'
        if filename.casefold() in filenames:
            raise ValueError('Canonical filename conflict')
        filenames.add(filename.casefold())
        payload = readable_path(source).read_bytes()
        pages = json.loads(payload.decode('utf-8-sig'))
        if not isinstance(pages, list) or not pages:
            raise ValueError(f'Invalid page-only source: {source.name}')
        seen = set()
        for page in pages:
            if (not isinstance(page, dict) or set(page) != {'text', 'page_idx'}
                    or type(page.get('page_idx')) is not int or page['page_idx'] < 0
                    or not isinstance(page.get('text'), str) or page['page_idx'] in seen):
                raise ValueError(f'Invalid page-only source: {source.name}')
            seen.add(page['page_idx'])
        with pdfium_lock:
            pdf = pdfium.PdfDocument(str(readable_path(originals[identity])))
            try:
                count = len(pdf)
            finally:
                pdf.close()
        if seen != set(range(count)):
            raise ValueError(f'PDF/GT page coverage mismatch: {source.name}')
        digest = hashlib.sha256(payload).hexdigest()
        dest = target/filename
        if readable_path(dest).exists() and sha(dest) != digest:
            raise ValueError(f'Existing document conflict: {filename}')
        digests[digest].append(filename)
        rows.append({'filename': filename, 'source': identity.as_posix()+'.json',
                     'pdf': identity.as_posix()+'.pdf', 'sha256': digest,
                     'pdf_sha256': sha(originals[identity]), 'pages': count,
                     'text_pages': sum(bool(p['text'].strip()) for p in pages)})
    # Preflight is complete. Copy exact bytes, atomically, without overwriting.
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for row in rows:
        dest = readable_path(target/row['filename'])
        source = gt/row['source']
        if dest.exists():
            if sha(dest) != row['sha256']:
                raise ValueError('Document changed after preflight')
            continue
        payload = readable_path(source).read_bytes()
        if hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise ValueError('Source changed after preflight')
        temporary = dest.with_suffix('.sync-tmp')
        temporary.write_bytes(payload)
        temporary.replace(dest)
        copied += 1
    mapping = {row['filename']: {'source_hash': row['sha256'],
               'relative_path': row['pdf'], 'sha256': row['pdf_sha256']} for row in rows}
    mapping_path = target/'ohr_sources.json'
    serialized = json.dumps(mapping, ensure_ascii=False, sort_keys=True)
    if not mapping_path.exists() or mapping_path.read_text(encoding='utf-8') != serialized:
        temporary = mapping_path.with_suffix('.tmp')
        temporary.write_text(serialized, encoding='utf-8')
        temporary.replace(mapping_path)
    return {'documents': len(rows), 'pages': sum(r['pages'] for r in rows),
            'copied': copied, 'categories': dict(Counter(r['source'].split('/')[0] for r in rows)),
            'blank_documents': [r['filename'] for r in rows if not r['text_pages']],
            'duplicate_content_groups': [names for names in digests.values() if len(names)>1],
            'qa_indexed': False, 'noise_indexed': False, 'sources': rows}


def validate_index(data, report):
    sources = {s['filename']: s for s in data['sources'].values()}
    skipped = {s['filename']: s for s in data.get('skipped_sources', [])}
    grouped = defaultdict(list)
    ids = set()
    for chunk in data['chunks']:
        if chunk['chunk_id'] in ids:
            raise ValueError('Duplicate chunk ID')
        ids.add(chunk['chunk_id'])
        grouped[chunk['filename']].append(chunk)
    for row in report['sources']:
        filename = row['filename']
        if not row['text_pages']:
            if filename not in skipped or skipped[filename]['source_hash'] != row['sha256']:
                raise ValueError(f'Blank source missing: {filename}')
            continue
        source = sources.get(filename)
        if not source or source['source_hash'] != row['sha256']:
            raise ValueError(f'Indexed source missing or changed: {filename}')
        pages = {p['page_no']: p['text'] for p in source['pages']}
        for chunk in grouped[filename]:
            if pages[chunk['page_no']][chunk['start_char']:chunk['end_char']] != chunk['text']:
                raise ValueError(f'Invalid source offset: {filename}')
        expected = {n for n, text in pages.items() if text.strip()}
        actual = {c['page_no'] for c in grouped[filename]}
        if expected != actual:
            raise ValueError(f'Indexed page coverage differs: {filename}')
    if data['errors']:
        raise ValueError('Index contains parse errors')
    return {'documents': len(sources), 'chunks': len(ids), 'skipped_sources': list(skipped),
            'errors': data['errors'], 'mode': data['mode'], 'fingerprint': data['fingerprint'],
            'all_source_hashes_and_chunk_offsets_checked': True}


def main():
    from app.config import get_settings
    from app.rag.indexer import get_local_index
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if settings.documents_path.resolve() != (args.root/'documents').resolve():
        raise ValueError('Selected OHR root is not the active documents directory')
    report = sync_corpus(args.root)
    report['ingestion'] = validate_index(get_local_index(), report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sources'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
