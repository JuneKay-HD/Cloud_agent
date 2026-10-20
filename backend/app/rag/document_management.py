"""Recoverable knowledge removal; no parsing, OCR or model calls."""
import hashlib
import shutil
from datetime import datetime, timezone
from uuid import uuid4

from app.config import get_settings
from app.rag import indexer
from app.rag.semantic_index import atomic_json


def delete_documents(doc_ids):
    ids = set(doc_ids)
    if not ids:
        raise ValueError('请先选择要删除的文档')
    with indexer._LOCK:
        settings = get_settings()
        root = settings.documents_path.resolve()
        index = indexer.read_serving_index()
        if not ids.issubset(index['sources']):
            raise KeyError('文档列表已变化，请刷新后重新选择')
        sources = {key: index['sources'][key] for key in ids}
        paths = []
        for source in sources.values():
            name = source['filename']
            path = root / name
            if not name or path.name != name or path.is_symlink() or path.resolve().parent != root:
                raise ValueError('文档路径无效，已取消删除')
            if not path.is_file():
                raise ValueError('原文件已变化，请重新构建索引后再操作')
            expected = source.get('source_hash')
            if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('原文件内容已变化，请重新构建索引后再删除')
            paths.append(path)
        # Compute the new immutable index without changing the cached snapshot.
        chunks = [c for c in index['chunks'] if c['doc_id'] not in ids]
        kept = {c['chunk_id'] for c in chunks}
        lexical = index.get('lexical_index')
        if lexical:
            lengths = {key: n for key, n in lexical['lengths'].items() if key in kept}
            postings = {}
            for term, matches in lexical['postings'].items():
                filtered = {key: n for key, n in matches.items() if key in kept}
                if filtered:
                    postings[term] = filtered
            lexical = {**lexical, 'postings': postings, 'lengths': lengths,
                       'average_length': sum(lengths.values()) / max(1, len(lengths))}
        else:
            lexical = indexer.build_lexical_index(chunks)
        data = {**index, 'sources': {k: v for k, v in index['sources'].items() if k not in ids},
                'chunks': chunks, 'lexical_index': lexical,
                'mode': 'lexical' if chunks else 'unavailable'}
        batch_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:12]
        archive = root / '.deleted' / batch_id
        archive.mkdir(parents=True)
        moved = []
        try:
            atomic_json(archive / 'manifest.json', {
                'batch_id': batch_id, 'deleted_at': datetime.now(timezone.utc).isoformat(),
                'documents': [{'doc_id': key, 'filename': source['filename']} for key, source in sources.items()],
                'note': '原文件保留于本目录；恢复到知识库目录后重新构建索引。共享原页、解析副本与历史对话不删除。'})
            for path in paths:
                destination = archive / path.name
                shutil.move(str(path), str(destination))
                moved.append((path, destination))
            data['fingerprint'] = indexer.corpus_fingerprint(root)
            indexer._publish_index(data)
        except Exception:
            for path, destination in reversed(moved):
                shutil.move(str(destination), str(path))
            raise
        indexer.load_persisted_index.cache_clear()
        return {'deleted_ids': sorted(ids), 'deleted_count': len(ids), 'batch_id': batch_id,
                'message': '文档已移出知识库，原文件已保留在本地回收目录。'}
