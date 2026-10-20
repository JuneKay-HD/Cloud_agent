"""Atomic local evidence index, with optional real cloud embeddings."""
import asyncio
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4
from copy import deepcopy
from datetime import datetime, timezone

from app.config import get_settings
from app.local_paths import readable_path
from app.rag.chunker import chunk_pages
from app.rag.parsers import parse_document, SUPPORTED_SUFFIXES
from app.rag.lexical import build_lexical_index, update_lexical_index

INDEX_VERSION = 15
SUPPORTED = SUPPORTED_SUFFIXES
_LOCK = threading.RLock()


_INDEX_CACHE = {}

def load_persisted_index(path: str, modified: int, size: int) -> dict:
    """Share one decoded snapshot, including an index just published in this process."""
    with _LOCK:
        key = (path, modified, size)
        if _INDEX_CACHE.get("key") != key:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            _INDEX_CACHE.update(key=key, data=data)
        return _INDEX_CACHE["data"]


def _clear_index_cache() -> None:
    with _LOCK:
        _INDEX_CACHE.clear()


load_persisted_index.cache_clear = _clear_index_cache


def supported_document(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED or path.name.lower().endswith(".ohr.json")


def corpus_fingerprint(root: Path) -> str:
    # Sort filenames independently of Windows' optional extended-path prefix.
    # Preserve the original Windows case-insensitive order across relocations.
    paths = sorted((readable_path(p) for p in root.iterdir() if readable_path(p).is_file() and (supported_document(p) or p.name in {"source_metadata.json", "ohr_sources.json"})), key=lambda p: p.name.lower()) if root.exists() else []
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in paths)).hexdigest()


def infer_metadata(text: str) -> dict:
    """Only explicit applicability statements count; arbitrary years in prose do not."""
    metadata = {}
    match = re.search(r"适用(?:年度)?\s*(20\d{2})(?:年度|年)?", text)
    if match:
        year = int(match.group(1))
        metadata.update(valid_from=f"{year}-01-01", valid_to=f"{year + 1}-01-01")
    match = re.search(r"自\s*(20\d{2})年(\d{1,2})月(\d{1,2})日起生效", text)
    if match:
        year, month, day = map(int, match.groups())
        metadata.update(valid_from=f"{year:04d}-{month:02d}-{day:02d}")
    match = re.search(r"实体标识\s*([A-Za-z][A-Za-z ]*?)(?:[；;，,。\n]|$)", text)
    if match:
        metadata["entity_ids"] = [match.group(1).strip()]
    match = re.search(r"版本\s*([A-Za-z0-9_.-]+)", text)
    if match:
        metadata["version"] = match.group(1)
    return metadata


def _read_index(*, rebuild=True):
    settings = get_settings()
    path = settings.indexes_path / "knowledge_index.json"
    parser_config = {"enable_ocr": settings.enable_ocr, "ocr_min_confidence": settings.ocr_min_confidence,
                     "ocr_max_pages": settings.ocr_max_pages}
    from app.rag.ocr import OCR_VERSION
    parser_config.update(ocr_version=OCR_VERSION, vision_model=settings.vision_model,
                         ocr_max_tokens=settings.ocr_max_tokens)
    from app.rag.document_assessment import load_rules
    parser_config['assessment_rules'] = load_rules()['fingerprint']
    with _LOCK:
        fingerprint = corpus_fingerprint(settings.documents_path)
        if path.exists():
            try:
                stat = path.stat()
                data = load_persisted_index(str(path),stat.st_mtime_ns,stat.st_size)
                if (data.get("version") == INDEX_VERSION and data.get("fingerprint") == fingerprint
                        and data.get("parser_config") == parser_config
                        and (not rebuild or not data.get("errors"))):
                    return data
            except (ValueError, OSError):
                pass
        if not rebuild:
            return {'version': INDEX_VERSION, 'fingerprint': fingerprint, 'chunks': [], 'sources': {},
                    'errors': [{'filename': 'knowledge_index.json', 'reason': '索引缺失或版本过期，请单独运行索引构建任务'}],
                    'mode': 'unavailable'}
        chunks, sources, errors, skipped_sources = [], {}, [], []
        catalog_path = settings.documents_path / "source_metadata.json"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
            if not isinstance(catalog, dict):
                raise ValueError("source_metadata.json 必须是文件名到元数据的映射")
        except (ValueError, OSError) as exc:
            # Invalid policy metadata cannot silently become unrestricted.
            return {"version": INDEX_VERSION, "fingerprint": fingerprint, "chunks": [], "sources": {},
                    "errors": [{"filename": "source_metadata.json", "reason": str(exc)}], "mode": "unavailable"}
        paths = sorted(readable_path(p) for p in settings.documents_path.iterdir()) if settings.documents_path.exists() else []
        mapping_path = settings.documents_path / 'ohr_sources.json'
        try:
            originals = json.loads(mapping_path.read_text(encoding='utf-8')) if mapping_path.exists() else {}
            if not isinstance(originals, dict):
                raise ValueError('OHR original mapping must be an object')
        except (OSError, ValueError) as exc:
            return {'version': INDEX_VERSION, 'fingerprint': fingerprint, 'chunks': [], 'sources': {},
                    'errors': [{'filename': 'ohr_sources.json', 'reason': str(exc)}], 'mode': 'unavailable'}
        for source in paths:
            if not source.is_file() or not supported_document(source):
                continue
            assessment = {}
            try:
                pages = parse_document(source)
                assessment = getattr(pages,'assessment',{})
                usable = any(p["text"].strip() for p in pages)
                if not usable:
                    if source.name.endswith('.ohr.json'):
                        skipped_sources.append({'filename':source.name,
                            'reason':'官方校对文本为空，保留原件，不建立文字证据',
                            'source_hash':hashlib.sha256(source.read_bytes()).hexdigest(),'pages':len(pages)})
                        continue
                    raise ValueError("未提取到可用文字，可能是扫描件；需要OCR或可读版本")
                managed_upload = bool(re.fullmatch(r"upload_[0-9a-f]{32}_.+", source.name))
                created = chunk_pages(source, pages, hard_limit=managed_upload)
                if not created:
                    raise ValueError("没有可用正文片段")
                metadata = infer_metadata("\n".join(c['text'] for c in created if not c.get('ocr_untrusted')))
                explicit = catalog.get(source.name, {})
                if not isinstance(explicit, dict):
                    raise ValueError("文档元数据必须为对象")
                allowed = {"valid_from", "valid_to", "entity_ids", "version", "authority"}
                if set(explicit) - allowed:
                    raise ValueError("文档元数据含未知字段")
                metadata.update(explicit)
                for key in ("version", "authority"):
                    if key in metadata and (not isinstance(metadata[key], str) or not metadata[key].strip()):
                        raise ValueError(f"{key} 必须为非空字符串")
                from datetime import date
                for key in ("valid_from", "valid_to"):
                    if key in metadata:
                        date.fromisoformat(metadata[key])
                if metadata.get("valid_from") and metadata.get("valid_to") and metadata["valid_from"] >= metadata["valid_to"]:
                    raise ValueError("文档适用时间区间无效")
                if "entity_ids" in metadata and (not isinstance(metadata["entity_ids"], list) or not all(isinstance(v, str) for v in metadata["entity_ids"])):
                    raise ValueError("entity_ids 必须为字符串列表")
                for chunk in created:
                    if managed_upload:
                        chunk["title"] = display_source_name(source.name)
                        if chunk["section"] == source.stem:
                            chunk["section"] = Path(display_source_name(source.name)).stem
                    chunk["source_metadata"] = dict(metadata)
                    # Markdown region sections provide a narrower entity scope.
                    if chunk["section"] in {"USA", "Canada", "China", "France", "Germany"}:
                        chunk["source_metadata"]["entity_ids"] = [chunk["section"]]
                source_record = {"filename": source.name,
                                 "title": display_source_name(source.name) if managed_upload else source.stem,
                                               "pages": pages, "source_hash": created[0]["source_hash"],
                                               "metadata": metadata, **getattr(pages,'assessment',{})}
                original = originals.get(source.name)
                if original is not None:
                    relative = Path(original['relative_path'])
                    if (not source.name.endswith('.ohr.json') or original['source_hash'] != created[0]['source_hash']
                            or relative.is_absolute() or len(relative.parts) != 2 or '..' in relative.parts
                            or relative.suffix.lower() != '.pdf'
                            or not re.fullmatch(r'[0-9a-f]{64}', original['sha256'])):
                        raise ValueError('Invalid OHR original PDF mapping')
                    source_record['original_pdf'] = {
                        'relative_path': relative.as_posix(), 'sha256': original['sha256']}
                from app.rag.markdown_output import save_markdown
                markdown_path = settings.parsed_path / (created[0]['doc_id'] + '.md')
                save_markdown(markdown_path, pages)
                source_record['markdown_sha256'] = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
                sources[created[0]['doc_id']] = source_record
                chunks.extend(created)
            except Exception as exc:
                errors.append({"filename": source.name, "reason": f"{type(exc).__name__}: {exc}",
                               **{k:v for k,v in getattr(exc,'assessment',assessment).items() if k!='pages'}})
        data = {"version": INDEX_VERSION, "fingerprint": fingerprint, "chunks": chunks,
                "sources": sources, "errors": errors, "mode": "lexical" if chunks else "unavailable",
                "skipped_sources":skipped_sources,
                "parser_config": parser_config, "lexical_index": build_lexical_index(chunks)}
        settings.indexes_path.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        stat = path.stat()
        _INDEX_CACHE.update(key=(str(path), stat.st_mtime_ns, stat.st_size), data=data)
        return data


def get_local_index() -> dict:
    return _read_index()


def read_serving_index() -> dict:
    """Request paths never parse/rebuild a corpus or call embedding services."""
    return _read_index(rebuild=False)


def knowledge_health(data: dict | None = None) -> dict:
    try:
        if data is None:
            data = read_serving_index()
        settings = get_settings()
        examples_path = settings.config_path / "demo_examples.json"
        examples = json.loads(examples_path.read_text(encoding="utf-8")) if examples_path.exists() else []
        mode = data["mode"]
        from app.rag.semantic_index import semantic_health
        semantic = semantic_health(data)
        if semantic['status']=='ready' and settings.dashscope_api_key and settings.bailian_host:
            mode = 'hybrid'
        from app.rag.diagnostics import knowledge_issues
        issues = knowledge_issues(data)
        ohr_count = sum(s['filename'].endswith('.ohr.json') for s in data['sources'].values())
        blank_ohr = sum(s['filename'].endswith('.ohr.json') for s in data.get('skipped_sources', []))
        label = settings.demo_data_label
        if ohr_count and 'OHR-Bench' in label:
            label = re.sub(r'OHR-Bench[：:][^；\n]*',
                           f'OHR-Bench：{ohr_count + blank_ohr}份官方校对文本（{ohr_count}份可检索，'
                           f'{blank_ohr}份空文本），非本地OCR结果', label)
        return {"status": "partial" if data["chunks"] and issues else "ready" if data["chunks"] else "unavailable",
                "mode": mode, "documents": len(data["sources"]), "chunks": len(data["chunks"]),
                "source_files":len(data['sources'])+len(data.get('skipped_sources',[])),
                "skipped_sources":data.get('skipped_sources',[]),
                "issues": issues, "fingerprint": data["fingerprint"],
                "lexical_algorithm": "bm25", "semantic_index": semantic,
                "formats": {suffix: sum(Path(s["filename"]).suffix.lower() == suffix for s in data["sources"].values())
                            for suffix in sorted(SUPPORTED)},
                "ohr_formats": {".ohr.json": sum(s["filename"].endswith(".ohr.json") for s in data["sources"].values())},
                "ocr_pages": sum(p.get("extraction_method") == "ocr" for s in data["sources"].values() for p in s["pages"]),
                "data_label": label, "dataset_profile": settings.dataset_profile, "examples": examples,
                "ohr_documents": ohr_count}
    except Exception as exc:
        return {"status": "unavailable", "mode": "unavailable", "documents": 0, "chunks": 0,
                "issues": [{"reason": f"{type(exc).__name__}: {exc}"}]}


async def build_index(use_embeddings: bool = False) -> dict:
    data = await asyncio.to_thread(get_local_index)
    from app.rag.search_text import SEARCH_TEXT_VERSION
    if data.get('search_text_version')!=SEARCH_TEXT_VERSION and data['chunks']:
        data = {**data, 'lexical_index':build_lexical_index(data['chunks']), 'search_text_version':SEARCH_TEXT_VERSION}
        from app.rag.semantic_index import atomic_json
        atomic_json(get_settings().indexes_path/'knowledge_index.json',data)
    result = {"documents": len(data["sources"]), "chunks": len(data["chunks"]),
              "mode": data["mode"], "issues": data["errors"]}
    if not data["chunks"]:
        return result
    if use_embeddings:
        from app.rag.semantic_index import build_semantic_index
        result['semantic_build'] = await build_semantic_index(data)
        result["mode"] = "hybrid"
    if get_settings().enable_region_layout:
        result['region_layout'] = await asyncio.to_thread(_build_region_layouts, data)
    return result


def display_source_name(filename: str) -> str:
    return re.sub(r'^upload_[0-9a-f]{32}_', '', filename)


def _publish_index(data: dict) -> None:
    settings = get_settings()
    data["fingerprint"] = corpus_fingerprint(settings.documents_path)
    data["mode"] = "lexical" if data["chunks"] else "unavailable"
    target = settings.indexes_path / "knowledge_index.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temporary, target)
        stat = target.stat()
        _INDEX_CACHE.update(key=(str(target), stat.st_mtime_ns, stat.st_size), data=data)
    finally:
        temporary.unlink(missing_ok=True)


def save_uploaded_source(name: str, content: bytes, pages: list[dict], chunks: list[dict]) -> str:
    """Persist the original and already parsed evidence without reparsing the corpus."""
    with _LOCK:
        data = dict(get_local_index())
        if "parser_config" not in data:
            raise ValueError("知识库元数据无效，请修复后再上传")
        root = get_settings().documents_path.resolve()
        root.mkdir(parents=True, exist_ok=True)
        filename = f"upload_{uuid4().hex}_{name}"
        target = readable_path(root / filename)
        source_hash = hashlib.sha256(content).hexdigest()
        doc_id = hashlib.sha256((filename + source_hash).encode()).hexdigest()[:24]
        created = deepcopy(chunks)
        for chunk in created:
            chunk.update(doc_id=doc_id, filename=filename, title=name)
            chunk["chunk_id"] = hashlib.sha256(
                f"{doc_id}:{chunk['page_no']}:{chunk['start_char']}:{chunk['end_char']}".encode()).hexdigest()[:24]
        data["sources"] = dict(data["sources"])
        source_record = {"filename": filename, "title": name, "pages": pages,
                         "source_hash": source_hash, "metadata": dict(created[0].get("source_metadata", {})),
                         **getattr(pages, 'assessment', {})}
        markdown_path = get_settings().parsed_path / (doc_id + '.md')
        data["sources"][doc_id] = source_record
        data["chunks"] = [*data["chunks"], *created]
        data["lexical_index"] = update_lexical_index(data.get("lexical_index"), created, [])
        from app.rag.search_text import SEARCH_TEXT_VERSION
        data['search_text_version'] = SEARCH_TEXT_VERSION
        from app.rag.markdown_output import save_markdown
        try:
            with target.open("xb") as handle:
                handle.write(content)
            save_markdown(markdown_path, pages)
            data['sources'][doc_id]['markdown_sha256'] = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
            _publish_index(data)
        except Exception:
            target.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            raise
        return doc_id


def delete_knowledge_sources(doc_ids: list[str]) -> dict:
    """Validate the whole selection, archive originals, then atomically publish evidence."""
    with _LOCK:
        data = dict(get_local_index())
        ids = list(dict.fromkeys(doc_ids))
        if not ids or any(key not in data["sources"] for key in ids):
            raise KeyError("所选文件已更新或不存在，请刷新知识库后重新选择")
        root = get_settings().documents_path.resolve()
        paths = []
        for key in ids:
            source = data["sources"][key]
            candidate = (root / source["filename"]).resolve()
            if candidate.parent != root or not readable_path(candidate).is_file():
                raise ValueError("原件不可用或路径不在知识库中，未删除任何文件")
            if hashlib.sha256(readable_path(candidate).read_bytes()).hexdigest() != source["source_hash"]:
                raise ValueError("原件已变化，请刷新后重新选择，未删除任何文件")
            paths.append(candidate)
        archive = root / ".deleted" / uuid4().hex
        archive.mkdir(parents=True)
        moved = []
        try:
            (archive / "manifest.json").write_text(json.dumps({
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "documents": [{"doc_id": key, "filename": data["sources"][key]["filename"],
                    "source_hash": data["sources"][key]["source_hash"]} for key in ids],
            }, ensure_ascii=False), encoding="utf-8")
            for candidate in paths:
                destination = archive / candidate.name
                os.replace(readable_path(candidate), readable_path(destination))
                moved.append((candidate, destination))
            selected = set(ids)
            data["sources"] = {key: value for key, value in data["sources"].items() if key not in selected}
            removed = [chunk for chunk in data["chunks"] if chunk["doc_id"] in selected]
            data["chunks"] = [chunk for chunk in data["chunks"] if chunk["doc_id"] not in selected]
            if len(removed) > len(data["chunks"]):
                data["lexical_index"] = build_lexical_index(data["chunks"])
            else:
                data["lexical_index"] = update_lexical_index(data.get("lexical_index"), [], removed)
            _publish_index(data)
        except Exception:
            for candidate, destination in reversed(moved):
                os.replace(readable_path(destination), readable_path(candidate))
            raise
        return {"deleted_ids": ids, "deleted_count": len(ids), "recoverable": True}
def _build_region_layouts(data: dict) -> dict:
    """Import-time region side tables; failures never reach the index build."""
    from app.rag.region_build import build_for_knowledge_source, store_dir
    settings = get_settings()
    log = {}
    for doc_id, source in data.get('sources', {}).items():
        chunks = [c for c in data.get('chunks', []) if c.get('doc_id') == doc_id]
        try:
            log[doc_id] = build_for_knowledge_source({**source, '_chunks': chunks}, settings)
        except Exception as exc:
            log[doc_id] = {'step': 'region_layout', 'status': 'failed',
                           'reason': f'{type(exc).__name__}: {exc}'}
    try:
        import json as _json
        from app.rag.semantic_index import atomic_json
        atomic_json(store_dir(settings) / 'build-log.json', _json.loads(_json.dumps(log)))
    except Exception:
        pass
    completed = sum(1 for entry in log.values() if entry.get('status') == 'completed')
    return {'completed': completed, 'failed': sum(1 for entry in log.values() if entry.get('status') == 'failed'),
            'skipped': sum(1 for entry in log.values() if entry.get('status') == 'skipped')}
