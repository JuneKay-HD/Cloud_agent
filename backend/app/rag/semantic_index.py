"""Resumable real embeddings with strict manifests and atomic activation."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import numpy as np

from app.config import get_settings
from app.rag.search_text import search_text, SEARCH_TEXT_VERSION

FORMAT = 1


def provider_id():
    from app.clients.bailian_chat import _compatible_base
    return hashlib.sha256(_compatible_base(get_settings().bailian_host).encode()).hexdigest()


def manifest(index):
    s = get_settings()
    return {'format':FORMAT,'fingerprint':index['fingerprint'],'model':s.embedding_model,
            'dimensions':s.embedding_dimensions,'provider':provider_id(),'search_text_version':SEARCH_TEXT_VERSION,
            'chunk_ids':[c['chunk_id'] for c in index['chunks']],
            'input_digest':hashlib.sha256(''.join(hashlib.sha256(search_text(c).encode()).hexdigest()
                                                for c in index['chunks']).encode()).hexdigest()}


@lru_cache(maxsize=2)
def _archive(path, modified, size):
    with np.load(path, allow_pickle=False) as archive:
        meta = json.loads(str(archive['metadata']))
        vectors = archive['vectors'].astype(np.float32)
    if (vectors.ndim!=2 or not vectors.shape[1] or not np.isfinite(vectors).all() or
        not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-4)):
        raise ValueError('invalid_or_unnormalized_vectors')
    vectors.flags.writeable = False
    return meta,vectors


@lru_cache(maxsize=2)
def _expected(path, modified, size, fingerprint, model, dimensions, provider, version):
    # Reuse text hashing for an immutable, persisted corpus snapshot.
    from app.rag.indexer import load_persisted_index
    return manifest(load_persisted_index(path,modified,size))


def load_semantic_index(index):
    s = get_settings()
    path = s.indexes_path/'knowledge_vectors.npz'
    stat = path.stat()
    meta,vectors = _archive(str(path),stat.st_mtime_ns,stat.st_size)
    corpus = s.indexes_path/'knowledge_index.json'
    if corpus.exists():
        cs = corpus.stat()
        expected = _expected(str(corpus),cs.st_mtime_ns,cs.st_size,index['fingerprint'],s.embedding_model,
                             s.embedding_dimensions,provider_id(),SEARCH_TEXT_VERSION)
    else:
        expected = manifest(index)
    if expected['fingerprint']!=index['fingerprint']:
        raise ValueError('semantic_corpus_fingerprint_mismatch')
    if any(meta.get(key)!=value for key,value in expected.items()):
        raise ValueError('semantic_manifest_mismatch')
    if vectors.shape!=(len(meta['chunk_ids']),s.embedding_dimensions):
        raise ValueError('semantic_shape_mismatch')
    return meta,vectors


def semantic_health(index):
    try:
        meta, vectors = load_semantic_index(index)
        return {'status':'ready','model':meta['model'],'dimensions':meta['dimensions'],
                'chunks':len(vectors),'build':meta.get('build',{})}
    except FileNotFoundError:
        return {'status':'missing','reason':'请单独运行 ingest_documents.py --embeddings'}
    except (ValueError,KeyError,OSError,TypeError):
        return {'status':'stale_or_invalid','reason':'向量索引与活动正文、模型或维度不一致，请显式重建'}


def atomic_json(path, data):
    temporary = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    replace_file(temporary,path)


def replace_file(temporary, target):
    # Windows readers/virus scanners may briefly hold a destination handle.
    for attempt in range(6):
        try:
            os.replace(temporary,target)
            return
        except PermissionError:
            if attempt==5: raise
            time.sleep(.05 * 2**attempt)


async def build_semantic_index(index, *, embed_batch=None):
    from app.clients.bailian_embedding import EmbeddingClient
    from app.rag.indexer import corpus_fingerprint
    s = get_settings()
    s.indexes_path.mkdir(parents=True,exist_ok=True)
    lock = s.indexes_path/'semantic-build.lock'
    # OS lock is automatically released after interruption; the lock file stays.
    handle = lock.open('a+b')
    try:
        handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError('semantic_build_already_running') from None
    started = time.perf_counter()
    meta = manifest(index)
    status_path = s.indexes_path/'semantic-build.json'
    status = {'status':'running','fingerprint':index['fingerprint'],'chunks':len(index['chunks']),
              'completed':0,'cached':0,'calls':0,'tokens':0,'model':s.embedding_model}
    database = sqlite3.connect(s.indexes_path/'embedding-cache.sqlite')
    try:
        database.execute('CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)')
        namespace = json.dumps({k:meta[k] for k in ('model','dimensions','provider','search_text_version')},sort_keys=True)
        texts = list(dict.fromkeys(search_text(c) for c in index['chunks']))
        keys = {t:hashlib.sha256((namespace+'\0'+t).encode()).hexdigest() for t in texts}
        rows, missing = {}, []
        for text in texts:
            found = database.execute('SELECT vector FROM embeddings WHERE key=?',(keys[text],)).fetchone()
            row = np.frombuffer(found[0],dtype=np.float32) if found else None
            if row is not None and row.shape==(s.embedding_dimensions,) and np.isfinite(row).all() and abs(float(np.linalg.norm(row))-1)<1e-4:
                rows[text] = row
            else:
                missing.append(text)
        status.update(unique_texts=len(texts),cached=len(rows),completed=len(rows))
        atomic_json(status_path,status)
        queue = asyncio.Queue()
        for start in range(0,len(missing),10):
            queue.put_nowait(missing[start:start+10])
        async with EmbeddingClient() as client:
            request = embed_batch or client.batch
            async def worker():
                while not queue.empty():
                    batch = queue.get_nowait()
                    values = np.asarray(await request(batch),dtype=np.float32)
                    if (values.shape!=(len(batch),s.embedding_dimensions) or not np.isfinite(values).all() or
                        (np.linalg.norm(values,axis=1)<=1e-12).any()):
                        raise ValueError('invalid_embedding_batch')
                    values /= np.linalg.norm(values,axis=1,keepdims=True)
                    for text,row in zip(batch,values):
                        rows[text] = row
                        database.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?)',(keys[text],row.tobytes()))
                    database.commit()
                    status.update(completed=len(rows),calls=client.calls,tokens=client.tokens,
                                  elapsed_seconds=round(time.perf_counter()-started,2))
                    atomic_json(status_path,status)
            tasks = [asyncio.create_task(worker()) for _ in range(s.embedding_build_concurrency)]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        # Reject activation if source files changed while the independent job ran.
        if corpus_fingerprint(s.documents_path)!=index['fingerprint']:
            raise ValueError('corpus_changed_during_embedding_build')
        vectors = np.stack([rows[search_text(c)] for c in index['chunks']])
        status.update(status='completed',elapsed_seconds=round(time.perf_counter()-started,2))
        meta['build'] = status
        target = s.indexes_path/'knowledge_vectors.npz'
        temporary = target.with_name(target.name+'.'+uuid4().hex+'.tmp')
        with temporary.open('wb') as output:
            np.savez(output,vectors=vectors,metadata=json.dumps(meta))
            output.flush(); os.fsync(output.fileno())
        replace_file(temporary,target)
        atomic_json(status_path,status)
        _archive.cache_clear(); _expected.cache_clear()
        return status
    except BaseException as exc:
        status.update(status='failed',error_type=type(exc).__name__,reason=str(exc)[:160],
                      elapsed_seconds=round(time.perf_counter()-started,2))
        atomic_json(status_path,status)
        raise
    finally:
        database.close(); handle.close()
