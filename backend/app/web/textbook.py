"""教材仅随 C 语言学生会话按小节在线读取。"""
from __future__ import annotations

from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

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


def _annotations(s: Session, user: m.User, row: m.TextbookSection) -> list[dict]:
    message = m.ChatMessage
    asked = aliased(message)
    question = (select(asked.content).where(
        asked.user_id == user.student_id, asked.course == "C", asked.role == "student",
        asked.server_id.is_(None), asked.problem_id.is_(None),
        asked.topic_id.is_not_distinct_from(message.topic_id), asked.id < message.id)
        .order_by(asked.id.desc()).limit(1).correlate(message).scalar_subquery())
    messages = s.execute(select(message, question).where(
        message.user_id == user.student_id, message.course == "C", message.role == "agent",
        message.server_id.is_(None), message.problem_id.is_(None),
        message.content["textbook"].as_string().is_not(None)).order_by(message.id.desc()))
    anchors = {item["id"]: item for item in row.anchors}
    annotations = []
    for reply, question_content in messages:
        refs = (reply.content or {}).get("textbook", [])
        for index, ref in enumerate(refs):
            if ref.get("edition") != row.edition_id or ref.get("section") != row.number:
                continue
            hit = anchors.get(ref.get("anchor"))
            if not hit or not hit.get("boxes"):
                continue
            question_text = "\n".join(block.get("text", "") for block in (question_content or {}).get("blocks", [])
                                      if block.get("t") == "p")
            annotations.append({
                "id": f"{reply.id}-{index}", "time": reply.created_at.isoformat(),
                "question": question_text, "explanation": ref.get("explanation", ""), "role": ref.get("role", "key"),
                "highlight": textbook.highlight(s, row, hit, ref.get("figureAnchor")),
            })
    return annotations


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
def section(edition: int, number: int, response: Response, anchor: int | None = None, figure: int | None = None,
            user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    row = _section(s, user, edition, number)
    hit = next((item for item in row.anchors if item["id"] == anchor), None) if anchor is not None else None
    response.headers.update(PRIVATE)
    pages = [{"url": f"/api/textbook/{edition}/sections/{number}/images/{i}",
              "width": page["width"], "height": page["height"]} for i, page in enumerate(row.pages)]
    return {"title": row.title, "pages": pages, "highlight": textbook.highlight(s, row, hit, figure) if hit else None,
            "annotations": _annotations(s, user, row)}


@router.get("/{edition}/sections/{number}/images/{index}")
def image(edition: int, number: int, index: int, top: int | None = None, bottom: int | None = None,
          user: m.User = Depends(student),
          s: Session = Depends(get_session)) -> Response:
    row = _section(s, user, edition, number)
    if not 0 <= index < len(row.pages):
        raise _not_found()
    picture = s.get(m.TextbookFigure, (edition, row.pages[index]["image"]))
    if picture is None:
        raise _not_found()
    png = picture.png
    if top is not None or bottom is not None:
        if top is None or bottom is None or not 0 <= top < bottom <= row.pages[index]["height"]:
            raise _not_found()
        with Image.open(BytesIO(png)) as original, BytesIO() as output:
            original.crop((0, top, original.width, bottom)).save(output, "PNG")
            png = output.getvalue()
    return Response(png, media_type="image/png", headers={
        **PRIVATE, "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
