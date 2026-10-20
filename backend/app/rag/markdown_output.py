"""Persist readable extraction separately from structured source/audit records."""
import os
from pathlib import Path
from uuid import uuid4
from app.local_paths import readable_path


def document_markdown(pages):
    if len(pages) == 1:
        return pages[0]['text']
    return '\n\n---\n\n'.join(
        f"<!-- 原件页码：{p['page_no']} -->\n\n{p['text']}" for p in pages)


def save_markdown(path: Path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = readable_path(path.with_name(path.stem + '.' + uuid4().hex[:8] + '.tmp'))
    try:
        temporary.write_text(document_markdown(pages), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
