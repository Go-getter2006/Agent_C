"""教材仅随 C 语言学生会话按小节在线读取。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .. import textbook
from .deps import get_session, student

router = APIRouter(prefix="/api/textbook")
PRIVATE = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


def _not_found() -> HTTPException:
    return HTTPException(404, detail="找不到这节教材。", headers=PRIVATE)


def _section(s: Session, user: m.User, edition: int, number: int) -> m.TextbookSection:
    row = s.get(m.TextbookSection, (edition, number)) if user.course == "C" else None
    if row is None:
        raise _not_found()
    return row


@router.get("/contents")
def contents(response: Response, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    response.headers.update(PRIVATE)
    edition = textbook.active(s) if user.course == "C" else None
    if edition is None:
        return {"book": None}
    sections = s.execute(select(m.TextbookSection.number, m.TextbookSection.parent, m.TextbookSection.title)
                         .where(m.TextbookSection.edition_id == edition.id).order_by(m.TextbookSection.number))
    return {"book": {"edition": edition.id, "title": edition.title,
                     "sections": [{"id": n, "parent": p, "title": t} for n, p, t in sections]}}


@router.get("/{edition}/sections/{number}")
def section(edition: int, number: int, response: Response, anchor: int | None = None,
            user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    row = _section(s, user, edition, number)
    hit = next((item for item in row.anchors if item["id"] == anchor), None) if anchor is not None else None
    response.headers.update(PRIVATE)
    pages = [{"url": f"/api/textbook/{edition}/sections/{number}/images/{i}",
              "width": page["width"], "height": page["height"]} for i, page in enumerate(row.pages)]
    return {"title": row.title, "pages": pages, "highlight": (
        {"boxes": hit["boxes"], "figure": hit.get("figure", False), "text": hit["text"]} if hit else None)}


@router.get("/{edition}/sections/{number}/images/{index}")
def image(edition: int, number: int, index: int, user: m.User = Depends(student),
          s: Session = Depends(get_session)) -> Response:
    row = _section(s, user, edition, number)
    if not 0 <= index < len(row.pages):
        raise _not_found()
    picture = s.get(m.TextbookFigure, (edition, row.pages[index]["image"]))
    if picture is None:
        raise _not_found()
    return Response(picture.png, media_type="image/png", headers={
        **PRIVATE, "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
