from app.rag.section_location import citation_section
import contextlib
from hashlib import sha256
from io import BytesIO
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.local_paths import readable_path
from app.rag.chunker import chunk_pages
from app.rag.indexer import (read_serving_index as get_local_index, knowledge_health, infer_metadata,
                             save_uploaded_source, delete_knowledge_sources, display_source_name)
from app.rag.parsers import parse_document, SUPPORTED_SUFFIXES
from app.rag.markdown_output import save_markdown, document_markdown
from app.storage.sessions import add_document, document_summary, get_session, update_session
from app.rag.document_assessment import DocumentAssessmentError

router = APIRouter(tags=["documents"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = SUPPORTED_SUFFIXES
FORMAT_MESSAGE = '支持 PDF、PPT/PPTX、DOCX、TXT、Markdown 和 PNG/JPEG/WebP/BMP/TIFF 图片'


@contextlib.contextmanager
def _scoped_knowledge_base(knowledge_base_id: str | None):
    """Scope a document/health endpoint to the explicitly selected knowledge
    base. Unknown ids are rejected (no silent default); absent ids fall back to
    the active profile so legacy calls keep working."""
    from app.sources import SourceError, scoped_selection
    if not knowledge_base_id:
        yield None
        return
    try:
        with scoped_selection(knowledge_base_id=knowledge_base_id, enforce=True) as selection:
            yield selection
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from None


def _source_versions(source):
    return {'source_hash': source.get('source_hash') or source.get('original_sha256'),
            'original_hash': source.get('original_sha256') or
            (source.get('original_pdf') or {}).get('sha256') or source.get('source_hash')}


def _check_versions(source, expected_source_hash=None, expected_original_hash=None):
    versions = _source_versions(source)
    for key, expected in (('source_hash', expected_source_hash), ('original_hash', expected_original_hash)):
        if expected is not None and expected != versions[key]:
            raise HTTPException(409, '引用原件版本已变化，请重新读取来源')
    return versions


def _page_version_query(source):
    from urllib.parse import urlencode
    return '&' + urlencode({'expected_' + key: value for key, value in _source_versions(source).items() if value})


def _upload_path(session_id: str, document_id: str, suffix: str = '.pdf') -> Path:
    # Hash identifiers so request parameters can never escape the upload directory.
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError('附件存储格式无效')
    return readable_path(get_settings().sessions_path.parent / "uploads" /
            sha256(session_id.encode()).hexdigest() / (sha256(document_id.encode()).hexdigest() + suffix))


def _session_document(session_id: str, document_id: str) -> dict:
    try:
        state = get_session(session_id)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None
    document = next((d for d in state["documents"] if d["id"] == document_id), None)
    if document is None:
        raise HTTPException(404, "附件不属于当前对话")
    return document


def _original_path(session_id: str, document: dict) -> Path:
    from app.local_paths import readable_path
    suffix = document.get('original_suffix') or ('.' + document.get('type', 'pdf'))
    path = readable_path(_upload_path(session_id, document['id'], suffix))
    expected = document.get('original_sha256') or document.get('original_file_sha256')
    if document.get('knowledge_doc_id') and (not expected or not path.is_file()):
        _, source = resolve_source(document['knowledge_doc_id'])
        if expected and source['source_hash'] != expected:
            raise HTTPException(409, '原始附件已变化，请重新上传后核对引用')
        path = _knowledge_original(source)
        expected = source['source_hash']
    if not expected or not path.is_file():
        raise HTTPException(404, '此旧附件未保留原始文件，请重新上传；仍可查看已解析正文')
    if _file_hash(path) != expected:
        raise HTTPException(409, '原始附件已变化，请重新上传后核对引用')
    return path


def _file_hash(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except PermissionError:
        raise HTTPException(403, '后端没有读取原件的权限，请检查文件访问权限') from None
    except OSError:
        raise HTTPException(503, '原件暂不可读取，请确认文件存在且可访问后重新加载') from None


def _original_pdf(session_id: str, document: dict) -> Path:
    path = _original_path(session_id, document)
    if path.suffix.lower() != '.pdf':
        raise HTTPException(404, '该附件不是PDF')
    return path


def _stored_page_response(pages, page_no):
    import base64
    page = next((p for p in pages if p.get('page_no') == page_no), None)
    uri = (page.get('source_image') or page.get('formula_image') or '') if page else ''
    if not uri.startswith('data:image/png;base64,'):
        raise HTTPException(404, '未保留该页原图')
    try:
        content = base64.b64decode(uri.split(',', 1)[1], validate=True)
    except ValueError:
        raise HTTPException(409, '原页图像记录无效') from None
    return Response(content, media_type='image/png', headers={'Cache-Control': 'private, no-store'})


def _render_pdf_page(path: Path, page_no: int) -> Response:
    import pypdfium2 as pdfium
    from app.rag.pdf_runtime import pdfium_lock
    with pdfium_lock, pdfium.PdfDocument(str(path)) as pdf:
        if page_no < 1 or page_no > len(pdf):
            raise HTTPException(404, "原始PDF中不存在该页")
        page = pdf[page_no - 1]
        try:
            width, height = page.get_size()
            bitmap = page.render(scale=min(2, 2200 / max(width, height)))
            try:
                output = BytesIO()
                bitmap.to_pil().convert("RGB").save(output, format="PNG")
                return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "private, no-store"})
            finally:
                bitmap.close()
        finally:
            page.close()


def _pdf_page_response(path: Path, page_no: int) -> Response:
    try:
        return _render_pdf_page(path, page_no)
    except HTTPException:
        raise
    except (ImportError, OSError):
        raise HTTPException(503, 'PDF渲染依赖未加载或原件暂不可读取，请检查后端运行环境后重新加载') from None
    except Exception as exc:
        if getattr(exc, 'err_code', None) == 4:
            raise HTTPException(422, '原件为加密PDF，请提供可读取的未加密版本') from None
        raise HTTPException(500, 'PDF原页渲染失败，请重新加载；持续失败时请重新上传可读取原件') from None




@router.get("/sessions/{session_id}/documents/{document_id}/page")
def session_original_page(session_id: str, document_id: str, page_no: int,
                          expected_source_hash: str | None = None, expected_original_hash: str | None = None):
    document = _session_document(session_id, document_id)
    _check_versions(document, expected_source_hash, expected_original_hash)
    if not (document.get('original_sha256') or document.get('original_file_sha256') or document.get('knowledge_doc_id')):
        # Old attachments may retain a real rendered page but not the source file.
        return _stored_page_response(document.get('pages', []), page_no)
    path = _original_path(session_id, document)
    if path.suffix.lower() == '.pdf':
        return _pdf_page_response(path, page_no)
    return _stored_page_response(document.get('pages', []), page_no)


@router.get('/sessions/{session_id}/documents/{document_id}/markdown')
def session_markdown(session_id: str, document_id: str):
    document = _session_document(session_id, document_id)
    path = _upload_path(session_id, document_id, '.md').with_name(sha256(document_id.encode()).hexdigest() + '-parsed.md')
    if not document.get('markdown_sha256') or not path.is_file():
        raise HTTPException(404, '未保存Markdown，请重新上传')
    if sha256(path.read_bytes()).hexdigest() != document['markdown_sha256']:
        raise HTTPException(409, 'Markdown已变化，请重新解析')
    return FileResponse(path, media_type='text/markdown; charset=utf-8', filename=Path(document['name']).stem + '.md')


def _original_document(session_id: str, document: dict) -> Path:
    return _original_path(session_id, document)


@router.get("/sessions/{session_id}/documents/{document_id}/file")
def session_original_file(session_id: str, document_id: str, download: bool = False):
    document = _session_document(session_id, document_id)
    media = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8"}.get(document.get("type"))
    return FileResponse(_original_document(session_id, document), media_type=media,
                        filename=document["name"], content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/sessions/{session_id}/documents/{document_id}/preview")
def session_document_preview(session_id: str, document_id: str):
    document = _session_document(session_id, document_id)
    base = f"/api/sessions/{session_id}/documents/{document_id}"
    original_note = None
    try:
        _original_document(session_id, document)
    except HTTPException as exc:
        original_note = exc.detail
    pages = document.get("pages", [])
    if not pages:
        # Do not present historical retrieval fragments as a complete document.
        pages = [{"page_no": c.get("page_no"), "text": c["text"]} for c in document.get("chunks", [])]
    return {"id": document_id, "name": document["name"], "type": document.get("type"),
            "file_url": None if original_note else base + "/file",
            "download_url": None if original_note else base + "/file?download=true",
            "original_note": original_note,
            "text_note": None if document.get("pages") else "此旧附件仅保留检索片段，下面不是完整正文。",
            "parsing_status": document.get("parsing_status", "ready"),
            "parsing_warnings": document.get("parsing_warnings", []),
            "pages": [{"page_no": p.get("page_no"), "text": p.get("text", ""),
                       "parsing_warnings": p.get("parsing_warnings", []),
                       "image_url": (f"{base}/page?page_no={p['page_no']}"
                                     if not original_note and document.get("type") == "pdf" and p.get("page_no") else None)}
                      for p in pages]}


def _parse_upload(name: str, content: bytes) -> tuple[list[dict], list[dict]]:
    with TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_bytes(content)
        pages = parse_document(path)
        if sum(len(page["text"]) for page in pages) > 100_000:
            audit=getattr(pages,'assessment',{})
            audit.setdefault('execution_record',[]).append({'step':'document_chunk','status':'failed',
                'reason':'文档文字超过10万字，未执行切片'})
            raise DocumentAssessmentError("文档文字超过 10 万字，请拆分后上传",audit)
        chunks = chunk_pages(path, pages, hard_limit=True)
        if not chunks:
            audit=getattr(pages,'assessment',{})
            audit.setdefault('execution_record',[]).append({'step':'document_chunk','status':'failed',
                'reason':'未提取到可用正文，不建立问答证据'})
            raise DocumentAssessmentError('未提取到可用正文，保留评估报告；请提供可读原件',audit)
        return pages, chunks


@router.post("/sessions/{session_id}/documents", status_code=201)
async def upload_document(session_id: str, file: UploadFile = File(...)) -> dict:
    try:
        state = get_session(session_id)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None
    try:
        name = (file.filename or "").replace("\\", "/").split("/")[-1]
        if not name or len(name) > 180 or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise HTTPException(415, FORMAT_MESSAGE)
        if len(state["documents"]) >= 10:
            raise HTTPException(400, "每个对话最多上传 10 个文件，请新建对话")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "单个文件不能超过 10 MB")
        if not content:
            raise HTTPException(422, "不能上传空文件")
        try:
            pages, chunks = await run_in_threadpool(_parse_upload, name, content)
        except DocumentAssessmentError as exc:
            assessment_id = str(uuid4())
            previous = get_session(session_id).get('document_assessments', [])
            record = {'assessment_id':assessment_id, 'filename':name, 'reason':str(exc),
                      **{k:v for k,v in exc.assessment.items() if k!='pages'}}
            suffix = Path(name).suffix.lower()
            original_path = _upload_path(session_id, assessment_id, suffix)
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(content)
            record.update(original_suffix=suffix, original_sha256=sha256(content).hexdigest(),
                          file_url=f'/api/sessions/{session_id}/document-assessments/{assessment_id}/file')
            update_session(session_id, document_assessments=[*previous[-19:], record])
            raise HTTPException(422, {'message':str(exc), 'assessment':record,
                'assessment_url':f'/api/sessions/{session_id}/document-assessments/{assessment_id}'}) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(422, "无法读取文件，请检查文档是否损坏、加密或编码不为 UTF-8") from None
        if not chunks:
            raise HTTPException(422, "未提取到文字；扫描版 PDF 请先进行文字识别")
        document_id = str(uuid4())
        metadata = infer_metadata("\n".join(c["text"] for c in chunks if not c.get('ocr_untrusted')))
        for index, chunk in enumerate(chunks):
            chunk.update(chunk_id=f"{document_id}-{index:03d}", doc_id=document_id, title=name)
            chunk["source_metadata"] = dict(metadata)
            if chunk["section"] in {"USA", "Canada", "China", "France", "Germany"}:
                chunk["source_metadata"]["entity_ids"] = [chunk["section"]]
        document = dict(id=document_id, name=name, size=len(content),
                        type=Path(name).suffix.lower().lstrip("."), chunk_count=len(chunks), chunks=chunks, pages=pages,
                        parsing_status='partial' if any(p.get('parsing_warnings') for p in pages) else 'ready',
                        parsing_warnings=[w for p in pages for w in p.get('parsing_warnings', [])])
        original_path = _upload_path(session_id, document_id, "." + document["type"])
        try:
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(content)
        except Exception:
            original_path.unlink(missing_ok=True)
            raise HTTPException(503, "原始文件保存失败，文件未上传成功，请重试") from None
        document["original_file_sha256"] = sha256(content).hexdigest()
        document["original_sha256"] = sha256(content).hexdigest()
        document.update(getattr(pages, 'assessment', {}))
        document['original_suffix'] = Path(name).suffix.lower()
        if document['original_suffix'] == '.pdf' and get_settings().enable_region_layout:
            try:
                from app.rag.region_build import build_for_upload
                summary = await run_in_threadpool(build_for_upload, original_path, pages, chunks, content)
            except Exception as exc:
                summary = {'step': 'region_layout', 'status': 'failed',
                           'reason': f'{type(exc).__name__}: {exc}'}
            document.setdefault('execution_record', []).append(summary)
        # Keep the original .md attachment separate from the derived extraction.
        markdown_path = _upload_path(session_id, document_id, '.md').with_name(sha256(document_id.encode()).hexdigest() + '-parsed.md')
        try:
            save_markdown(markdown_path, pages)
        except Exception:
            original_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            raise HTTPException(503, '解析文件保存失败，已撤销本次上传，请重试') from None
        document['markdown_sha256'] = sha256(markdown_path.read_bytes()).hexdigest()
        document['markdown_url'] = f'/api/sessions/{session_id}/documents/{document_id}/markdown'
        try:
            knowledge_id = await run_in_threadpool(save_uploaded_source, name, content, pages, chunks)
        except Exception:
            original_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            raise HTTPException(503, "知识库保存失败，文件未上传成功，请重试") from None
        document["knowledge_doc_id"] = knowledge_id
        try:
            add_document(session_id, document)
        except Exception as exc:
            if document.get("original_sha256"):
                original_path.unlink(missing_ok=True)
            markdown_path.unlink(missing_ok=True)
            try:
                await run_in_threadpool(delete_knowledge_sources, [knowledge_id])
            except Exception as rollback_error:
                raise HTTPException(503, "会话保存失败，知识库回滚未完成，请检查知识库后重试") from rollback_error
            raise HTTPException(503, "会话保存失败，已撤销本次入库，请重试") from exc
        return document_summary(document)
    finally:
        await file.close()


@router.get("/sessions/{session_id}/documents/{document_id}/source")
def session_source_excerpt(session_id: str, document_id: str, chunk_id: str, quote: str | None = None) -> dict:
    try:
        state = get_session(session_id)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None
    document = next((item for item in state["documents"] if item["id"] == document_id), None)
    if document is None:
        raise HTTPException(404, "附件不属于当前对话")
    chunk = next((item for item in document["chunks"] if item["chunk_id"] == chunk_id), None)
    if chunk is None:
        raise HTTPException(404, "未找到对应的引用位置")
    page = next((p for p in document.get("pages", []) if p["page_no"] == chunk["page_no"]), None)
    text = page["text"] if page else chunk["text"]
    start, end = (chunk["start_char"], chunk["end_char"]) if page else (0, len(text))
    return {
        "page_image_url": (f"/api/sessions/{session_id}/documents/{document_id}/page?page_no={chunk['page_no']}" + _page_version_query(document)
                           if document.get("original_sha256") and chunk.get("page_no") else None),
        "file_url": (f"/api/sessions/{session_id}/documents/{document_id}/file"
                     if document.get("original_file_sha256") or document.get("original_sha256") else None),
        "formula_image": page.get("formula_image") if page else None,
        "source_image": page.get('source_image') if page else None,
        "original_unavailable": document.get("type") in {'pdf', 'ppt', 'pptx', 'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tif', 'tiff'} and not document.get("original_sha256") and not (page and page.get("formula_image")),
        "title": document["name"],
        "filename": document["name"],
        "page_no": chunk.get("page_no"),
        "section": chunk.get("section"),
        **citation_section(document.get("pages", []), chunk, quote),
        "before": text[:start],
        "highlight": text[start:end],
        "after": text[end:],
        "start_char": start, "end_char": end,
        "extraction_method": chunk.get("extraction_method", "text"),
        "ocr_min_confidence": chunk.get("ocr_min_confidence"),
        "text_format": chunk.get('text_format', 'text'),
        "ocr_validation": chunk.get('ocr_validation'),
        "ocr_boxes": page.get("ocr_boxes", []) if page else [],
        "ocr_uncertain_ranges": page.get('ocr_uncertain_ranges', []) if page else [],
        "parsing_warnings": page.get('parsing_warnings', []) if page else [],
        "ocr_untrusted": chunk.get('ocr_untrusted', False),
        **_source_versions(document),
    }


def _report_summary(report):
    """List payload: aggregate fields without per-page detail arrays.

    The dialog loads full per-page reports through the detail endpoint; the list
    only carries fields needed to render summary chips and the “查看文档分析质量”
    entry (has-report / not-yet-analyzed / not-applicable states)."""
    if not isinstance(report, dict):
        return None
    return {key: value for key, value in report.items() if key != 'pages'}


class DeleteDocumentsRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=5000)


def _delete_knowledge_documents(doc_ids, knowledge_base_id):
    from app.rag.document_management import delete_documents
    with _scoped_knowledge_base(knowledge_base_id):
        try:
            return delete_documents(doc_ids)
        except KeyError as exc:
            raise HTTPException(409, str(exc.args[0])) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except OSError:
            raise HTTPException(503, '文件暂时被占用或写入失败，删除未完成，请稍后重试') from None


@router.post('/documents/batch-delete')
def batch_delete_documents(request: DeleteDocumentsRequest, knowledge_base_id: str | None = None):
    return _delete_knowledge_documents(request.doc_ids, knowledge_base_id)


@router.delete('/documents/{doc_id}')
def delete_knowledge_document(doc_id: str, knowledge_base_id: str | None = None):
    return _delete_knowledge_documents([doc_id], knowledge_base_id)


@router.get("/documents")
def list_documents(knowledge_base_id: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        root = get_settings().documents_path
        return [{"doc_id": key, "name": display_source_name(source["filename"]), "metadata": source["metadata"],
                 "uploaded": bool(re.fullmatch(r"upload_[0-9a-f]{32}_.+", source["filename"])),
                 'markdown_url': f'/api/documents/{key}/markdown',
                 'complexity_report': _report_summary(source.get('complexity_report')),
                 'quality_report': _report_summary(source.get('quality_report')),
                 'execution_record': source.get('execution_record'),
                 'source_hash': source.get('source_hash') or source.get('original_sha256'),
                 'format': Path(source['filename']).suffix.lstrip('.').lower()}
                for key, source in get_local_index()["sources"].items()]


@router.get('/knowledge')
def knowledge_snapshot(knowledge_base_id: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        return {'health': knowledge_health(), 'documents': list_documents()}


def _text_checks(source: dict) -> dict:
    """Rule-based 繁体字/错别字 detection over stored pages (read-only).

    Runs synchronously over in-memory page text only; no model/OCR/embedding.
    Aggregates per-page counts and a flat candidate list for the dialog's
    “告警与校正” tab."""
    try:
        from app.rag.text_checks import aggregate_text_checks
        pages = source.get('pages') or []
        if not pages:
            return {'method_version': '', 'pages': [], 'traditional_count': 0,
                    'typo_count': 0, 'suspect_pages': [], 'reason': '无正文页面'}
        result = aggregate_text_checks(pages)
        from app.rag.knowledge_review import candidate_id
        for page in result['pages']:
            for candidate in [*page['traditional'], *page['typos']]:
                candidate['candidate_id'] = candidate_id(candidate)
        return result
    except Exception as exc:
        return {'method_version': '', 'pages': [], 'traditional_count': 0,
                'typo_count': 0, 'suspect_pages': [], 'reason': f'{type(exc).__name__}: {exc}'}


def analysis_state(source: dict) -> str:
    """Dialog state for one knowledge source, by what was actually recorded.

    - complete       complexity + quality reports present and finished
    - partial        a report is missing or reports an unknown/partial status
    - not_applicable quality report explicitly not_applicable (e.g. proofread
                     .ohr sources have no visual page to measure)
    - not_yet_analyzed no analysis reports exist yet
    Read-only: never triggers OCR/model/reindex; the “查看” entry only reads
    records that already exist."""
    complexity = source.get('complexity_report') or {}
    quality = source.get('quality_report') or {}
    has_c = bool(complexity)
    has_q = bool(quality)
    if not has_c and not has_q:
        return 'not_yet_analyzed'
    if has_q and quality.get('status') == 'not_applicable':
        return 'not_applicable'
    statuses = [s for s in (complexity.get('status'), quality.get('status')) if s]
    if not statuses or any(s in {'unknown', 'partial', 'blocked'} for s in statuses):
        return 'partial'
    return 'complete'


@router.get("/documents/{doc_id}/analysis")
def document_analysis(doc_id: str, knowledge_base_id: str | None = None):
    """Full per-page document analysis for the quality dialog.

    Scoped to the selected knowledge base by identity; never matches by filename
    alone. Returns 404 for unknown docs and a structured `state` for documents
    that were never analyzed (e.g. proofread-only .ohr sources). `text_checks`
    is read-only rule detection over the stored body (繁体字/错别字候选)，不触发
    模型或重建索引。"""
    with _scoped_knowledge_base(knowledge_base_id):
        index, source = resolve_source(doc_id)
        from app.rag.knowledge_review import cached_review, identity
        cached = None
        original_error = ''
        try:
            original = _knowledge_original(source)
            available = original.suffix.lower() == '.pdf' or original.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
            cached = cached_review(doc_id, source) if available else None
        except HTTPException as exc:
            available = False
            original_error = exc.detail
        reports = {}
        for name in ('complexity_report', 'quality_report', 'execution_record'):
            if name in source:
                reports[name] = source[name]
        if cached:
            reports = cached['reports']
        state = analysis_state(reports)
        if available and not cached:
            state = 'not_yet_analyzed'
        return {
            'doc_id': doc_id,
            'name': source['filename'],
            'title': source.get('title') or source['filename'],
            'metadata': source.get('metadata') or {},
            'source_hash': source.get('source_hash') or source.get('original_sha256'),
            'original_hash': _source_versions(source)['original_hash'],
            'text_hash': identity(source)['text_hash'],
            'markdown_sha256': source.get('markdown_sha256'),
            'format': Path(source['filename']).suffix.lstrip('.').lower(),
            'page_count': len(source.get('pages', [])),
            'chunk_count': sum(1 for c in index['chunks'] if c['doc_id'] == doc_id),
            'reports': reports,
            'text_checks': _text_checks(source),
            'state': state,
            'analysis_available': available,
            'analysis_cached': bool(cached),
            'analysis_reason': original_error,
            'assessment_target': cached.get('assessment_target') if cached else None,
            'original_filename': cached.get('original_filename') if cached else None,
            'recommendations': cached.get('recommendations', []) if cached else [],
        }


@router.post('/documents/{doc_id}/analysis')
async def analyze_document(doc_id: str, knowledge_base_id: str | None = None, force: bool = False):
    """Measure the verified original locally and persist a versioned side report."""
    with _scoped_knowledge_base(knowledge_base_id):
        _, source = resolve_source(doc_id)
        path = _knowledge_original(source)
        from app.rag.knowledge_review import analyze_original
        if path.suffix.lower() not in {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}:
            raise HTTPException(422, '该来源没有可评估的PDF或图像原件；文字检测仍可使用')
        try:
            await run_in_threadpool(analyze_original, doc_id, source, path, force=force)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, f'原件分析未完成：{exc}') from None
        return document_analysis(doc_id, knowledge_base_id)


@router.get('/sessions/{session_id}/document-assessments/{assessment_id}')
def failed_document_assessment(session_id: str, assessment_id: str):
    try:
        state = get_session(session_id)
    except KeyError:
        raise HTTPException(404, '对话不存在') from None
    record = next((r for r in state.get('document_assessments',[]) if r['assessment_id']==assessment_id),None)
    if record is None:
        raise HTTPException(404, '未找到该对话的文档评估')
    return record


@router.get('/sessions/{session_id}/document-assessments/{assessment_id}/file')
def failed_document_original(session_id: str, assessment_id: str):
    record = failed_document_assessment(session_id, assessment_id)
    path = _original_path(session_id, {'id': assessment_id, **record})
    return FileResponse(path, filename=record['filename'], content_disposition_type='attachment')


@router.get("/knowledge/health")
def health(knowledge_base_id: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        return knowledge_health()


def resolve_source(doc_id):
    index = get_local_index()
    source = index["sources"].get(doc_id)
    if source is None:
        raise HTTPException(404, "该引用已失效或文档已更新，请重新提问")
    return index, source


def _knowledge_original(source):
    from app.local_paths import readable_path
    root = get_settings().documents_path.resolve()
    canonical = (root/source['filename']).resolve()
    if canonical.parent != root or not readable_path(canonical).is_file():
        raise HTTPException(404, '原始文档不可用')
    if _file_hash(readable_path(canonical)) != source['source_hash']:
        raise HTTPException(409, '原始附件已变化，请重新建立索引')
    original = source.get('original_pdf')
    if not original:
        return readable_path(canonical)
    pdf_root = (root.parent/'pdfs').resolve()
    path = (pdf_root/original['relative_path']).resolve()
    if not path.is_relative_to(pdf_root) or path.suffix.lower() != '.pdf' or not readable_path(path).is_file():
        raise HTTPException(404, 'OHR原始PDF不可用，请恢复原件')
    if _file_hash(readable_path(path)) != original['sha256']:
        raise HTTPException(409, 'OHR原始PDF已变化，请重新核对并建立索引')
    return readable_path(path)


@router.get('/documents/{doc_id}/markdown')
def knowledge_markdown(doc_id: str, knowledge_base_id: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        _, source = resolve_source(doc_id)
        # doc_id must come from the actual source index, never from a client path.
        if not all(c in '0123456789abcdef' for c in doc_id) or len(doc_id) != 24:
            raise HTTPException(404, '文档索引标识无效')
        path = get_settings().parsed_path / (doc_id + '.md')
        if not path.is_file():
            raise HTTPException(404, '未保存Markdown，请重新建立索引')
        if not source.get('markdown_sha256') or sha256(path.read_bytes()).hexdigest() != source['markdown_sha256']:
            raise HTTPException(409, 'Markdown已变化，请重新建立索引')
        return FileResponse(path, media_type='text/markdown; charset=utf-8',
                            filename=Path(source['filename']).stem + '.md')


@router.get("/documents/{doc_id}/source")
def source_excerpt(doc_id: str, chunk_id: str, quote: str | None = None, knowledge_base_id: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        index, source = resolve_source(doc_id)
        chunk = next((c for c in index["chunks"] if c["doc_id"] == doc_id and c["chunk_id"] == chunk_id), None)
        if chunk is None:
            raise HTTPException(404, "未找到对应的引用位置")
        page = next(p for p in source["pages"] if p["page_no"] == chunk["page_no"])
        from urllib.parse import urlencode
        scope = urlencode({'knowledge_base_id': knowledge_base_id}) if knowledge_base_id else ''
        return {"page_image_url": (f"/api/documents/{doc_id}/page?page_no={chunk['page_no']}" + _page_version_query(source) + (f'&{scope}' if scope else '')
                                   if Path(source["filename"]).suffix.lower() == ".pdf" or source.get('original_pdf') or page.get('source_image') else None),
                "file_url": f"/api/documents/{doc_id}/file" + (f'?{scope}' if scope else ''),
                "source_image": page.get('source_image'), "formula_image": page.get('formula_image'),
                "title": source["title"], "filename": source["filename"], "page_no": chunk["page_no"],
                **citation_section(source["pages"], chunk, quote),
                "section": chunk["section"], "before": page["text"][:chunk["start_char"]],
                "highlight": page["text"][chunk["start_char"]:chunk["end_char"]],
                "after": page["text"][chunk["end_char"]:], **_source_versions(source),
                "start_char": chunk["start_char"], "end_char": chunk["end_char"],
                "extraction_method": page.get("extraction_method", "text"),
                "ocr_min_confidence": page.get("ocr_min_confidence"),
                "text_format": page.get('text_format', 'text'),
                "ocr_validation": chunk.get('ocr_validation'),
                "ocr_boxes": page.get("ocr_boxes", []),
                "ocr_uncertain_ranges": page.get('ocr_uncertain_ranges', []),
                "parsing_warnings": page.get('parsing_warnings', []),
                "ocr_untrusted": chunk.get('ocr_untrusted', False)}


@router.get("/documents/{doc_id}/page")
def knowledge_original_page(doc_id: str, page_no: int, knowledge_base_id: str | None = None,
                            expected_source_hash: str | None = None, expected_original_hash: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        _, source = resolve_source(doc_id)
        _check_versions(source, expected_source_hash, expected_original_hash)
        path = _knowledge_original(source)
        if path.suffix.lower() == '.pdf':
            return _pdf_page_response(path, page_no)
        return _stored_page_response(source.get('pages', []), page_no)


@router.get("/documents/{doc_id}/file")
def original_file(doc_id: str, knowledge_base_id: str | None = None):
    with _scoped_knowledge_base(knowledge_base_id):
        _, source = resolve_source(doc_id)
        path = _knowledge_original(source)
        media = "text/plain; charset=utf-8" if path.suffix.lower() in {".md", ".txt"} else None
        return FileResponse(readable_path(path), media_type=media, filename=path.name, content_disposition_type="inline")


@router.get("/sessions/{session_id}/documents/{document_id}/formula-page")
def formula_page_source(session_id: str, document_id: str, page_no: int):
    try:state=get_session(session_id)
    except KeyError:raise HTTPException(404,"对话不存在") from None
    doc=next((d for d in state['documents'] if d['id']==document_id),None)
    if doc is None:raise HTTPException(404,"附件不属于当前对话")
    page=next((p for p in doc.get('pages',[]) if p.get('page_no')==page_no),None)
    if page is None or not (page.get('formula_image') or doc.get('original_sha256')):raise HTTPException(404,"未保留该页原图，请重新上传文档")
    # highlight 必须来自实际视觉转写文本，而不是整页正文：转写结果与原页字形
    # 使用不同表示（LaTeX 命令/花括号等），引用偏移只对转写文本成立。
    transcription = page.get('visual_transcription') or page.get('text', '')
    return {'title':doc['name'],'filename':doc['name'],'page_no':page_no,
            'highlight':transcription,'before':'','after':'','formula_image':page.get('formula_image'),
            'file_url':f'/api/sessions/{session_id}/documents/{document_id}/file' if doc.get('original_sha256') else None,
            'page_image_url':f'/api/sessions/{session_id}/documents/{document_id}/page?page_no={page_no}' + _page_version_query(doc) if doc.get('original_sha256') else None,
            **_source_versions(doc),
            'transcription_source': 'visual_transcription' if page.get('visual_transcription') else 'page_text',
            'parsing_warnings':page.get('parsing_warnings',[]),'extraction_method':'原页图像，模型转写需核对'}

class SourceLocationRequest(BaseModel):
    page_no: int | None = Field(default=None, ge=1)
    quote: str=Field(min_length=1,max_length=4000)
    knowledge_base_id: str | None = Field(default=None, max_length=80)
    expected_source_hash: str | None = None
    expected_original_hash: str | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    chunk_id: str | None = Field(default=None, max_length=128)


class CorrectionItem(BaseModel):
    page_no: int | None = Field(default=None, ge=1, description='分页正文用实际页码；无分页正文(txt/markdown)传空')
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    replacement: str = Field(min_length=1, max_length=200)
    original: str | None = Field(default=None, max_length=200)
    basis: str = Field(default='', max_length=300)
    special_verify: bool = False
    confirmed: bool = True
    confirmed_by: str = Field(default='manual', max_length=80)


class CorrectionApplyRequest(BaseModel):
    confirmations: list[CorrectionItem] = Field(min_length=1, max_length=500)
    original_hash: str | None = Field(default=None, max_length=64)
    expected_text_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class KnowledgeCorrectionRequest(BaseModel):
    expected_source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_text_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_ids: list[str] = Field(min_length=1, max_length=500)


@router.post('/documents/{doc_id}/corrections')
async def correct_knowledge_document(doc_id: str, request: KnowledgeCorrectionRequest,
                                     knowledge_base_id: str | None = None):
    """Generate a reviewed copy. The shared evidence index remains the original."""
    with _scoped_knowledge_base(knowledge_base_id):
        _, source = resolve_source(doc_id)
        _check_versions(source, request.expected_source_hash)
        path = _knowledge_original(source)
        from app.rag.knowledge_review import identity
        ident = identity(source)
        if request.expected_text_hash != ident['text_hash']:
            raise HTTPException(409, '正文已变化，请重新读取并确认校正候选')
        checks = _text_checks(source)
        candidates = {c['candidate_id']: c for page in checks['pages']
                      for c in [*page['traditional'], *page['typos']]}
        if len(set(request.candidate_ids)) != len(request.candidate_ids) or any(i not in candidates for i in request.candidate_ids):
            raise HTTPException(422, '校正候选已失效或重复，请重新选择')
        edits = [{**candidates[i], 'replacement': candidates[i]['suggested'], 'confirmed': True}
                 for i in request.candidate_ids]
        from app.rag.corrections import apply_corrections, CorrectionError
        try:
            result = await run_in_threadpool(apply_corrections, source['pages'], edits,
                                             original_hash=ident['original_hash'])
        except CorrectionError as exc:
            raise HTTPException(422, str(exc)) from None
        version_id = uuid4().hex
        root = get_settings().indexes_path / 'document_corrections' / doc_id
        root.mkdir(parents=True, exist_ok=True)
        from app.rag.semantic_index import atomic_json
        from app.rag.markdown_output import save_markdown
        atomic_json(root / (version_id + '.json'), {**result, 'identity': ident, 'doc_id': doc_id})
        save_markdown(root / (version_id + '.md'), result['pages'])
        from urllib.parse import urlencode
        query = urlencode({'knowledge_base_id': knowledge_base_id}) if knowledge_base_id else ''
        return {'version_id': version_id, 'applied_count': len(result['change_log']),
                'change_log': result['change_log'], 'derived_text_hash': result['derived_text_hash'],
                'corrected_text': document_markdown(result['pages']),
                'download_url': f'/api/documents/{doc_id}/corrections/{version_id}/file?{query}',
                'serving_body_changed': False}


@router.get('/documents/{doc_id}/corrections/{version_id}/file')
def knowledge_correction_file(doc_id: str, version_id: str, knowledge_base_id: str | None = None):
    import json
    import re
    with _scoped_knowledge_base(knowledge_base_id):
        _, source = resolve_source(doc_id)
        if not re.fullmatch(r'[0-9a-f]{32}', version_id):
            raise HTTPException(404, '校正版本不存在')
        root = get_settings().indexes_path / 'document_corrections' / doc_id
        try:
            record = json.loads((root / (version_id + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise HTTPException(404, '校正版本不存在') from None
        if record['identity']['source_hash'] != source['source_hash']:
            raise HTTPException(409, '来源已更新，请重新核对校正版本')
        return FileResponse(root / (version_id + '.md'), media_type='text/markdown; charset=utf-8',
                            filename=Path(source['filename']).stem + '-corrected.md')


def _region_result(path, page, request, versions, pages, located):
    """Additive side-table resolution; None when no usable store exists."""
    if not path or Path(path).suffix.lower() != '.pdf':
        return None
    from app.rag.region_build import store_for
    from app.rag.region_store import LEVEL_NOTES, chunk_binding, page_record, regions_for_span
    store = store_for(pages, versions.get('original_hash'))
    if store is None:
        return None
    store_page = page_record(store, request.page_no)
    output = {'region_layout_version': store['region_layout_version'],
              'levels': list(LEVEL_NOTES)}
    if store_page is None:
        output.update(level='none', level_note='该页没有区域记录。', spans=[])
        return output
    output['page'] = {'width': store_page['width'], 'height': store_page['height'],
                      'rotation': store_page.get('rotation', 0)}
    start, end = located.get('start_char'), located.get('end_char')
    if located.get('text_status') == 'located' and isinstance(start, int) and isinstance(end, int):
        output.update(regions_for_span(store_page, page.get('text', ''), start, end))
        return output
    # Quote span unavailable: fall back to the import-time chunk binding,
    # capped at paragraph level — we know the area, not the exact quote.
    binding = chunk_binding(store, request.chunk_id) if request.chunk_id else None
    if binding and binding.get('spans'):
        region_by_id = {r['region_id']: r for r in store_page.get('regions', [])}
        spans = []
        for span in binding['spans']:
            rects = [region_by_id[i]['bbox'] for i in span.get('region_ids', [])
                     if i in region_by_id and region_by_id[i].get('bbox')]
            if rects:
                spans.append({'start_char': span['start_char'], 'end_char': span['end_char'],
                              'level': 'region', 'region_ids': span['region_ids'], 'rects': rects})
        if spans:
            output.update(level='region', level_note=LEVEL_NOTES['region'], spans=spans)
            return output
    output.update(level='none', level_note=LEVEL_NOTES['none'], spans=[])
    return output


def _quote_location(path, page, request, versions, pages):
    from app.rag.source_location import _valid_box, locate_quote, locate_text
    from app.rag.section_location import section_location
    result = locate_quote(path, page, request.quote, request.start_char, request.end_char,
                          cache_dir=get_settings().indexes_path / 'source_layout')
    if path is None and page.get('page_no') is None:
        parsed = locate_text(page, request.quote, request.start_char, request.end_char)
        result.update(parsed)
        result['text_status'] = 'located' if parsed['position_status'] == 'located_text' else 'not_found'
    if (result.get('original_hash') and versions.get('original_hash')
            and result['original_hash'] != versions['original_hash']):
        raise HTTPException(409, '定位期间原件版本发生变化，请重新读取来源')
    section = section_location(pages, {'page_no': request.page_no,
                                      'start_char': result.get('start_char') or 0})
    confirmed = result['text_status'] == 'located' and bool(section.get('section_heading'))
    response = {**result, **versions, 'heading_status': 'confirmed' if confirmed else 'unconfirmed',
            'section_heading': section.get('section_heading') if confirmed else None,
            'section_note': section.get('section_note') if confirmed else '该页未确认标题'}
    region = _region_result(path, page, request, versions, pages, result)
    if region is not None:
        response['region_result'] = region
        # Store fallback boxes for the classic panel when the legacy search
        # found nothing; preferred-source comparisons stay measurable.
        if (not result['highlight_boxes'] and region.get('spans')
                and region.get('level') in ('exact', 'partial')):
            from app.rag.region_store import percent_rects
            rects = [rect for span in region['spans'] for rect in span.get('rects', [])]
            boxes = [{**box, 'method': 'region_store_line'}
                     for box in percent_rects(region['page'], rects)]
            boxes = [box for box in boxes if _valid_box(box)]
            if boxes:
                response.update(highlight_boxes=boxes, position_status='located',
                                method='region_store_line', reason_code='located',
                                position_note=region['level_note'])
    return response

@router.post("/sessions/{session_id}/documents/{document_id}/location")
def locate_session_quote(session_id: str,document_id: str,request: SourceLocationRequest):
    doc=_session_document(session_id,document_id)
    versions=_check_versions(doc,request.expected_source_hash,request.expected_original_hash)
    page=next((p for p in doc.get('pages',[]) if p.get('page_no')==request.page_no),None)
    if page is None:raise HTTPException(404,"未找到引用页面")
    try:
        path=_original_path(session_id,doc) if doc.get('original_sha256') else None
    except HTTPException as error:
        if error.status_code != 404: raise
        path=None
    return _quote_location(path,page,request,versions,doc.get('pages',[]))


def _replace_session_document(session_id: str, document_id: str, **updates) -> dict:
    """Atomically replace one document record in the session, preserving order."""
    from app.storage.sessions import get_session as _get, update_session as _update
    state = _get(session_id)
    documents = state.get('documents', [])
    for index, document in enumerate(documents):
        if document['id'] == document_id:
            documents[index] = {**document, **updates}
            _update(session_id, documents=documents)
            return documents[index]
    raise HTTPException(404, "附件不属于当前对话")


@router.get("/sessions/{session_id}/documents/{document_id}/text-checks")
def session_text_checks(session_id: str, document_id: str) -> dict:
    """Read-only typo / traditional-Chinese candidate preview for an uploaded
    document. Never mutates; the dialog shows confirmation status (pending)."""
    document = _session_document(session_id, document_id)
    from app.rag.region_store import text_fingerprint
    from app.rag.markdown_output import document_markdown
    pages = document.get('pages', [])
    return {'document_id': document_id, 'name': document['name'],
            'original_hash': document.get('original_sha256'),
            'text_hash': text_fingerprint(pages),
            'pages': [{'page_no': p.get('page_no'), 'text': p.get('text', '')} for p in pages],
            'current_text': document_markdown(pages),
            'has_corrections': bool(document.get('corrections')) and not document['corrections'].get('reverted', False),
            'download_url': f'/api/sessions/{session_id}/documents/{document_id}/corrections/file',
            'text_checks': _text_checks(document)}


@router.post("/sessions/{session_id}/documents/{document_id}/corrections")
async def apply_document_corrections(session_id: str, document_id: str, request: CorrectionApplyRequest):
    """Apply human-confirmed corrections to an uploaded document.

    Produces a derived page set + change log, rebuilds chunks against the derived
    body, and — when the original is a PDF with region layout enabled — rebuilds
    the region side table against the derived text hash. The original body and
    its region mapping are preserved by construction (rollback via restore)."""
    document = _session_document(session_id, document_id)
    from app.rag.region_store import text_fingerprint
    if request.expected_text_hash and request.expected_text_hash != text_fingerprint(document.get('pages', [])):
        raise HTTPException(409, '正文已变化，请重新读取并确认校正候选')
    original_hash = request.original_hash or document.get('original_sha256')
    versions = _check_versions(document, None, original_hash)
    if original_hash != versions['original_hash']:
        raise HTTPException(409, "原件哈希不一致，拒绝建立校正派生版本")
    from app.rag.corrections import CorrectionError, apply_corrections
    try:
        path = _original_path(session_id, document)
    except HTTPException as error:
        raise HTTPException(409, '原始附件不可用，无法重建校正后的正文与引用切片；请重新上传原文件后再校正') from None
    try:
        result = await run_in_threadpool(apply_corrections, document.get('pages', []),
                                         [item.model_dump(exclude_none=True) for item in request.confirmations],
                                         original_hash=original_hash, path=path)
    except CorrectionError as exc:
        raise HTTPException(422, str(exc)) from None
    # Citation URLs identify the session attachment, while the text hash gives
    # every correction version distinct chunk identifiers.
    metadata = infer_metadata('\n'.join(p.get('text', '') for p in result['pages']))
    for index, chunk in enumerate(result['chunks']):
        chunk.update(doc_id=document_id, title=document['name'], source_metadata=metadata,
                     chunk_id=f"{document_id}-{result['derived_text_hash'][:12]}-{index:03d}")
    # Rebuild the region side table for the derived text when the original is a
    # PDF and layout is enabled; failures never block the correction itself.
    region_summary = None
    if path is not None and Path(path).suffix.lower() == '.pdf' and get_settings().enable_region_layout:
        try:
            from app.rag.region_build import build_and_save
            store = await run_in_threadpool(build_and_save, path, result['pages'], result['chunks'], original_hash)
            bound = sum(1 for c in store['chunks'].values() if c['level'] == 'exact')
            region_summary = {'status': 'completed', 'pages': len(store['pages']),
                              'exact_chunks': bound, 'total_chunks': len(store['chunks'])}
        except Exception as exc:
            region_summary = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
    previous = document.get('corrections') or {}
    if previous.get('reverted'):
        previous = {}
    change_log = [*previous.get('change_log', []), *result['change_log']]
    _replace_session_document(
        session_id, document_id,
        pages=result['pages'], chunks=result['chunks'], chunk_count=len(result['chunks']),
        original_sha256=original_hash,
        corrections={**{k: v for k, v in result.items() if k in ('version', 'original_hash', 'derived_text_hash', 'change_log')},
                     'change_log': change_log,
                     'original_pages': previous.get('original_pages', document.get('pages', [])),
                     'original_chunks': previous.get('original_chunks', document.get('chunks', [])),
                     'region_rebuild': region_summary})
    from app.rag.markdown_output import document_markdown
    return {**result,
            'document_id': document_id, 'name': document['name'],
            'corrected_text': document_markdown(result['pages']),
            'download_url': f'/api/sessions/{session_id}/documents/{document_id}/corrections/file',
            'region_rebuild': region_summary,
            'applied_count': len(result['change_log'])}


@router.get('/sessions/{session_id}/documents/{document_id}/corrections/file')
def session_corrected_file(session_id: str, document_id: str):
    document = _session_document(session_id, document_id)
    record = document.get('corrections') or {}
    if not record or record.get('reverted'):
        raise HTTPException(404, '没有可下载的校正正文')
    from app.rag.markdown_output import document_markdown
    from urllib.parse import quote
    name = quote(Path(document['name']).stem + '-corrected.md')
    return Response(document_markdown(document.get('pages', [])), media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{name}",
                             'Cache-Control': 'private, no-store'})


@router.post("/sessions/{session_id}/documents/{document_id}/corrections/restore")
async def restore_document_corrections(session_id: str, document_id: str) -> dict:
    """Roll a corrected document back to its original body.

    The pre-correction pages are preserved inside the correction record at apply
    time; this validates the change log against the original hash and switches
    the serving pages/chunks back to them."""
    document = _session_document(session_id, document_id)
    corrections = document.get('corrections', {})
    change_log = corrections.get('change_log')
    original_pages = corrections.get('original_pages')
    original_chunks = corrections.get('original_chunks')
    if not change_log or not original_pages:
        raise HTTPException(409, "没有可撤销的校正版本")
    original_hash = corrections.get('original_hash') or document.get('original_sha256')
    _check_versions(document, None, original_hash)
    from app.rag.corrections import restore_original
    restored = restore_original(original_pages, change_log, original_hash=original_hash)
    _replace_session_document(session_id, document_id,
                              pages=restored['pages'], chunks=original_chunks or [], chunk_count=len(original_chunks or []),
                              corrections={**corrections, 'reverted': True})
    return restored

@router.post("/documents/{doc_id}/location")
def locate_knowledge_quote(doc_id: str, request: SourceLocationRequest):
    with _scoped_knowledge_base(request.knowledge_base_id):
        index, source = resolve_source(doc_id)
        versions = _check_versions(source, request.expected_source_hash, request.expected_original_hash)
        page = next((p for p in source['pages'] if p.get('page_no') == request.page_no), None)
        if page is None: raise HTTPException(404, "未找到引用页面")
        try:
            path = _knowledge_original(source)
        except HTTPException as error:
            if error.status_code != 404: raise
            path = None
        if path is not None and path.suffix.lower() == '.pdf' and get_settings().enable_region_layout:
            from app.rag.region_build import ensure_knowledge_store
            import logging
            try:
                ensure_knowledge_store(source,
                    [chunk for chunk in index['chunks'] if chunk['doc_id'] == doc_id])
            except Exception:
                # The verified original page remains readable if geometry building fails.
                logging.getLogger(__name__).exception('引用区域映射构建失败: %s', doc_id)
        return _quote_location(path, page, request, versions, source['pages'])
