"""Persist local conversations and their document context in SQLite."""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from app.config import get_settings


@contextmanager
def _connection():
    path = get_settings().sessions_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        yield connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_state(session_id: str) -> dict:
    return dict(session_id=session_id, title="新对话", created_at=_now(), updated_at=_now(),
                pinned=False, title_manually_set=False,
                messages=[], documents=[], pending_message=None, last_task=None, last_result=None)


def create_session() -> str:
    session_id = str(uuid4())
    with _connection() as connection:
        connection.execute("INSERT INTO sessions VALUES (?, ?)",
                           (session_id, json.dumps(_new_state(session_id), ensure_ascii=False)))
    return session_id


from app.workflow.progress import observed

@observed('读取会话与附件')
def get_session(session_id: str) -> dict:
    with _connection() as connection:
        row = connection.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise KeyError(session_id)
    return json.loads(row[0])


def _mutate(session_id: str, mutation, *, touch: bool = True) -> dict:
    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        state = json.loads(row[0])
        mutation(state)
        if touch:
            state["updated_at"] = _now()
        connection.execute("UPDATE sessions SET state = ? WHERE id = ?",
                           (json.dumps(state, ensure_ascii=False), session_id))
        return state


def edit_session(session_id: str, *, title: str | None = None, pinned: bool | None = None) -> dict:
    def edit(state):
        if title is not None:
            state.update(title=title, title_manually_set=True)
        if pinned is not None:
            state["pinned"] = pinned
    return _summary(_mutate(session_id, edit, touch=False))


def delete_session(session_id: str) -> None:
    with _connection() as connection:
        if connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount == 0:
            raise KeyError(session_id)


def update_session(session_id: str, **values: object) -> None:
    _mutate(session_id, lambda state: state.update(values))


from app.workflow.progress import observed

@observed('更新会话与执行记录')
def append_message(session_id: str, role: str, text: str, **extra) -> None:
    def append(state):
        first_question = role == "user" and not any(item["role"] == "user" for item in state["messages"])
        state["messages"].append(dict(id=str(uuid4()), role=role, text=text, created_at=_now(), **extra))
        if first_question and not state.get("title_manually_set", False):
            state["title"] = text[:36]
    _mutate(session_id, append)


def add_document(session_id: str, document: dict) -> None:
    def append(state):
        state["documents"].append(document)
        if not state["messages"] and state["title"] == "新对话" and not state.get("title_manually_set", False):
            state["title"] = document["name"][:36]
    _mutate(session_id, append)


def document_summary(document: dict) -> dict:
    return {key: value for key, value in document.items() if key not in {"chunks", "pages"}}


def session_detail(session_id: str) -> dict:
    state = get_session(session_id)
    return {key: state[key] for key in ("session_id", "title", "created_at", "updated_at", "messages")} | {
        "pinned": state.get("pinned", False),
        "documents": [document_summary(doc) for doc in state["documents"]],
        "document_assessments": state.get('document_assessments', [])
    }


def _summary(state: dict) -> dict:
    return {key: state[key] for key in ("session_id", "title", "created_at", "updated_at")} | {
        "pinned": state.get("pinned", False),
        "message_count": len(state["messages"]), "document_count": len(state["documents"]),
        "assessment_count": len(state.get('document_assessments',[])),
    }


def list_sessions() -> list[dict]:
    with _connection() as connection:
        states = [json.loads(row[0]) for row in connection.execute("SELECT state FROM sessions")]
    return sorted([
        _summary(state)
        for state in states if state["messages"] or state["documents"] or state.get('document_assessments')
    ], key=lambda item: (item["pinned"], item["updated_at"]), reverse=True)
