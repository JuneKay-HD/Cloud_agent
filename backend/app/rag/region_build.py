"""Import-time region store builds, wired into indexing and uploads.

Failure-tolerant by contract: a region build problem is recorded as a
warning, never raised into the index build or the upload. Stores are side
tables; knowledge_index.json bytes and the semantic manifest stay untouched.
"""
from hashlib import sha256
from pathlib import Path
from threading import Lock

from app.config import get_settings
from app.rag import region_native, region_store

_ON_DEMAND_LOCK = Lock()


def store_dir(settings=None) -> Path:
    return (settings or get_settings()).indexes_path / 'region_layout'


def _sha256(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def knowledge_original_path(source: dict, settings=None):
    """Resolve the geometry original for a knowledge source, or None.

    Mirrors api.documents._knowledge_original without HTTP exceptions:
    OHR sources map to the real PDF under ../pdfs with hash verification;
    native PDFs verify against source_hash; other formats have no geometry.
    """
    settings = settings or get_settings()
    root = settings.documents_path
    original = source.get('original_pdf')
    try:
        if original:
            path = (root.parent / 'pdfs' / original['relative_path']).resolve()
            if not path.is_file() or path.suffix.lower() != '.pdf':
                return None
            return path if _sha256(path) == original['sha256'] else None
        path = (root / source['filename']).resolve()
        if path.suffix.lower() != '.pdf' or not path.is_file():
            return None
        return path if _sha256(path) == source['source_hash'] else None
    except OSError:
        return None


def build_and_save(pdf_path, pages, chunks, original_hash, settings=None) -> dict:
    settings = settings or get_settings()
    store = region_native.build_document_store(
        pdf_path, pages, chunks, original_hash,
        cache_dir=settings.indexes_path / 'source_layout')
    region_store.save_store(store_dir(settings), store)
    return store


def build_for_knowledge_source(source: dict, settings=None) -> dict:
    """Build the store for one knowledge source; summary for execution records."""
    settings = settings or get_settings()
    path = knowledge_original_path(source, settings)
    if path is None:
        return {'step': 'region_layout', 'status': 'skipped',
                'reason': '无可验证的原始PDF，未建立区域映射'}
    chunks = source.get('_chunks', [])
    store = build_and_save(path, source['pages'], chunks, _sha256(path), settings)
    bound = sum(1 for c in store['chunks'].values() if c['level'] == 'exact')
    return {'step': 'region_layout', 'status': 'completed',
            'region_layout_version': region_store.REGION_LAYOUT_VERSION,
            'pages': len(store['pages']), 'exact_chunks': bound,
            'total_chunks': len(store['chunks'])}


def build_for_upload(pdf_path: Path, pages, chunks, content: bytes, settings=None) -> dict:
    """Session upload: the temp file being parsed is the geometry original."""
    settings = settings or get_settings()
    store = build_and_save(pdf_path, pages, chunks, sha256(content).hexdigest(), settings)
    return {'step': 'region_layout', 'status': 'completed',
            'region_layout_version': region_store.REGION_LAYOUT_VERSION,
            'pages': len(store['pages'])}


def store_for(pages, original_hash: str, settings=None):
    """Request-time lookup: only an exact original+text version match is used."""
    if not original_hash:
        return None
    text_hash = region_store.text_fingerprint(pages)
    return region_store.load_store(store_dir(settings), original_hash, text_hash)


def ensure_knowledge_store(source: dict, chunks: list[dict], settings=None) -> dict:
    """Build a missing side table once when an original PDF becomes available.

    Indexing can run before a separately stored OHR PDF set is mounted. Citation
    lookup restores only the requested document's verified geometry and leaves
    the text index unchanged. Concurrent quote clicks share one build.
    """
    settings = settings or get_settings()
    original_hash = (source.get('original_pdf') or {}).get('sha256') or source.get('source_hash')
    if not settings.enable_region_layout or not original_hash:
        return {'step': 'region_layout', 'status': 'skipped'}
    if store_for(source['pages'], original_hash, settings) is not None:
        return {'step': 'region_layout', 'status': 'ready'}
    with _ON_DEMAND_LOCK:
        if store_for(source['pages'], original_hash, settings) is not None:
            return {'step': 'region_layout', 'status': 'ready'}
        return build_for_knowledge_source({**source, '_chunks': chunks}, settings)
