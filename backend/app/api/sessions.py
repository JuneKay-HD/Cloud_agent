from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator

from app.api.chat import _busy_sessions
from app.storage.sessions import create_session as create_session_record
from app.storage.sessions import delete_session as delete_session_record, edit_session
from app.storage.sessions import list_sessions as list_session_records, session_detail

router = APIRouter(tags=["sessions"])


class SessionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    pinned: StrictBool | None = None

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if value is not None:
            value = value.strip()
            if not value or len(value) > 80:
                raise ValueError("对话名称须为 1 至 80 个字符")
        return value

    @model_validator(mode="after")
    def has_changes(self):
        if self.title is None and self.pinned is None:
            raise ValueError("请提供对话名称或置顶状态")
        return self


@router.post("/sessions")
def create_session() -> dict[str, str]:
    return {"session_id": create_session_record(), "status": "created"}


@router.get("/sessions")
def list_sessions() -> list[dict]:
    return list_session_records()


@router.get("/sessions/{session_id}")
def read_session(session_id: str) -> dict:
    try:
        return session_detail(session_id)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, changes: SessionEdit) -> dict:
    try:
        return edit_session(session_id, title=changes.title, pinned=changes.pinned)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> Response:
    # Run the check and deletion without yielding to an active chat request.
    if session_id in _busy_sessions:
        raise HTTPException(409, "当前对话正在生成回答，请停止或等待回答完成后再删除")
    try:
        delete_session_record(session_id)
    except KeyError:
        raise HTTPException(404, "对话不存在") from None
    return Response(status_code=204)
