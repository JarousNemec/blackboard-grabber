from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class _Loose(BaseModel):
    """Base pro pydantic modely, které mají z Blackboard API spoustu polí navíc.

    `extra="ignore"` zaručuje, že nová/neznámá pole API neshodí parsing —
    klíčové pro odolnost proti změnám interního API.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class User(_Loose):
    id: str
    userName: str | None = None
    studentId: str | None = None
    given: str | None = None
    family: str | None = None


class Course(_Loose):
    id: str
    courseId: str
    name: str
    description: str | None = None


class Membership(_Loose):
    id: str = ""
    userId: str | None = None
    courseId: str
    courseRoleId: str | None = None
    course: Course | None = None


class ContentHandler(_Loose):
    id: str | None = None
    url: str | None = None
    targetId: str | None = None
    targetType: str | None = None


def _coerce_content_handler(v: Any) -> Any:
    """Blackboard vrací contentHandler v listingu jako string ('resource/x-bb-folder'),
    v detailu jako objekt {id, url, ...}. Sjednotíme na objekt."""
    if isinstance(v, str):
        return {"id": v}
    return v


def _coerce_body(v: Any) -> Any:
    """Body field může být string (legacy) nebo dict {rawText, displayText, fileType,
    webLocation}. Vyber displayText (renderovaný HTML), fallback rawText."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("displayText") or v.get("rawText") or None
    return None


class Attachment(_Loose):
    id: str
    fileName: str
    mimeType: str | None = None
    size: int | None = None


class ContentItem(_Loose):
    id: str
    title: str = ""
    body: Annotated[str | None, BeforeValidator(_coerce_body)] = None
    description: str | None = None
    position: int = 0
    hasChildren: bool = False
    hasGradebookColumns: bool = False
    contentHandler: Annotated[
        ContentHandler | None, BeforeValidator(_coerce_content_handler)
    ] = None
    contentDetail: dict[str, Any] | None = None
    # Z `?expand=fileAttachments` — attachmenty asociované s content itemem.
    fileAttachments: dict[str, Any] | None = None
    parentId: str | None = None
    availability: dict[str, Any] | None = None

    @property
    def is_folder_like(self) -> bool:
        """Detekce folder/lesson — Blackboard Ultra `hasChildren` neposílá,
        místo něj contentHandler nebo contentDetail.{handler}.isFolder."""
        if self.hasChildren:
            return True
        handler_id = (self.contentHandler.id if self.contentHandler else "") or ""
        if "x-bb-folder" in handler_id or "x-bb-lesson" in handler_id:
            return True
        if self.contentDetail:
            for sub in self.contentDetail.values():
                if isinstance(sub, dict) and sub.get("isFolder"):
                    return True
        return False


class Paging(_Loose):
    nextPage: str | None = None


class PagedResponse(_Loose):
    results: list[dict[str, Any]] = Field(default_factory=list)
    paging: Paging | None = None
