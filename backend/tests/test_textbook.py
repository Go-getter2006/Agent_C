from __future__ import annotations

import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app import db, llm, textbook
from app import models as m
from app.textbook_word import prepare
from app.web.app import create_app
from app.web.deps import student
from tests.test_pdftext import make_pdf

SENTENCE = "An array index starts at zero."
HEADINGS = ("1.1 Arrays", "1.1.1 Array index", "1.1.2 Array length")


def test_private_book_uses_exact_pdf_anchors_and_preserves_citations(demo, tmp_path):
    lines = [HEADINGS[0], HEADINGS[1], SENTENCE, HEADINGS[2], "The array length counts its elements."]
    source = tmp_path / "synthetic.docx"
    with ZipFile(source, "w") as docx:
        paragraphs = []
        for line in lines:
            outline = '<w:pPr><w:outlineLvl w:val="1"/></w:pPr>' if line == HEADINGS[0] else ""
            paragraphs.append(f"<w:p>{outline}<w:r><w:t>{line}</w:t></w:r></w:p>")
        docx.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                      'wordprocessingml/2006/main"><w:body>' + "".join(paragraphs) + "</w:body></w:document>")
    source.with_suffix(".pdf").write_bytes(make_pdf(lines))
    bundle = tmp_path / "synthetic.textbook.private.zip"
    report = prepare(bundle, "测试教材", [source], tmp_path)
    assert report["sections"] == 3 and report["citation_anchors"] == 2
    with db.session_scope() as s:
        row, changed = textbook.publish(s, bundle)
        edition = row.id
        assert changed and textbook.publish(s, bundle)[1] is False
        section = s.get(m.TextbookSection, (edition, 2))
        target = section.anchors[0]
        assert target["text"] == SENTENCE and target["boxes"]
        assert textbook.retrieve(s, "D", "array index", []) == {}
        assert textbook.retrieve(s, "C", "火星天气预报", []) == {}

    app = create_app()
    prefix = f"/api/textbook/{edition}/sections/2"
    image = prefix + "/images/0"
    calls = []

    def answer(messages, *_):
        sys, user = (item["content"] for item in messages[:2])
        if "intent 只能取" in sys:
            return '{"intent":"CONCEPT","icap":"active"}', 10, 10
        calls.append(user)
        return json.dumps({"before": "数组首元素的下标为 0。", "textbook": ["T1", "T999", "T1"]},
                          ensure_ascii=False), 10, 10

    with TestClient(app) as client:
        assert client.get(image).status_code == 401
        assert client.get("/api/textbook/contents").status_code == 401
        assert client.post("/api/auth/login", json={"loginName": "2500000001", "password": "test-pass"}).status_code == 200
        book = client.get("/api/textbook/contents")
        assert book.headers["cache-control"] == "private, no-store"
        assert [(x["parent"], x["title"]) for x in book.json()["book"]["sections"]] == [
            (None, HEADINGS[0]), (1, HEADINGS[1]), (1, HEADINGS[2])]
        assert "anchors" not in book.text
        assert client.get(prefix).json()["highlight"] is None
        assert client.get(prefix, params={"anchor": target["id"]}).json()["highlight"]["text"] == SENTENCE
        pic = client.get(image)
        assert pic.headers["content-type"] == "image/png"
        assert pic.headers["content-disposition"] == "inline"
        assert pic.headers["cache-control"] == "private, no-store"
        assert client.get(image.replace("/0", "/999")).status_code == 404
        for name in ("download", "original", "bundle"):
            assert client.get(f"/api/textbook/{edition}/{name}").status_code == 404

        llm.set_backend(answer)
        try:
            turn = client.post("/api/threads/new/messages", json={"text": "什么是 array index？"}).json()
            assert len(calls) == 1 and SENTENCE in calls[0]
            msg = turn["messages"][-1]
            assert msg["textbook"] == [{"edition": edition, "section": 2, "anchor": target["id"],
                                       "path": list(HEADINGS[:2])}]
            assert SENTENCE not in json.dumps(msg)
            history = client.get(f"/api/threads/{turn['thread']}").json()["messages"]
            assert history[-1]["textbook"] == msg["textbook"] and history[-1]["time"] == msg["time"]
        finally:
            llm.set_backend(None)

        with db.session_scope() as s:
            learner = s.scalar(m.User.__table__.select().with_only_columns(m.User.id).where(
                m.User.student_id == "2500000001"))
        assert learner is not None
        app.dependency_overrides[student] = lambda: m.User(id=learner, role="student", course="D")
        assert client.get("/api/textbook/contents").json()["book"] is None
        assert client.get(prefix).status_code == 404 and client.get(image).status_code == 404
