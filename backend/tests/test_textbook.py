from __future__ import annotations

import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

import app.textbook_word as textbook_word
from app import db, llm, textbook
from app import models as m
from app.textbook_word import prepare
from app.web.app import create_app
from app.web.deps import student
from tests.test_pdftext import make_pdf

SENTENCE = "An array index starts at zero."
HEADINGS = ("1.1 Arrays", "1.1.1 Array index", "1.1.2 Array length")


def test_exercise_numbers_stay_in_the_exercise_section(monkeypatch, tmp_path):
    paragraphs = [
        ("第1章 基础", None), ("1.1 基础语法", None), ("习　题　1", 1),
        ("1.4 编写程序并输出图形：", None), ("1.5 输出另一个图形", 1),
        ("第3章 流程控制", None), ("3.1 选择结构", None), ("习题三", None),
        ("3.12 有几种换法：", None), ("(1) 计算换法数量", None),
        ("第4章 函数", None), ("4.1 函数定义", None),
    ]
    source = tmp_path / "synthetic.docx"
    with ZipFile(source, "w") as docx:
        blocks = []
        for text, level in paragraphs:
            props = f'<w:pPr><w:outlineLvl w:val="{level}"/></w:pPr>' if level is not None else ""
            blocks.append(f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>")
        docx.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                      'wordprocessingml/2006/main"><w:body>' + "".join(blocks) + "</w:body></w:document>")

    extracted = []

    def render(_output, _title, sections, _pdf_directory):
        extracted.extend(sections)
        return {}

    monkeypatch.setattr(textbook_word, "render", render)
    prepare(tmp_path / "synthetic.zip", "测试教材", [source], tmp_path)
    assert [s["title"] for s in extracted] == [
        "第1章 基础", "1.1 基础语法", "习题1", "第3章 流程控制", "3.1 选择结构", "习题三",
        "第4章 函数", "4.1 函数定义",
    ]
    assert extracted[2]["parent"] == extracted[0]["id"]
    assert [b["parts"][0] for b in extracted[2]["blocks"]] == [paragraphs[3][0], paragraphs[4][0]]
    assert [b["parts"][0] for b in extracted[5]["blocks"]] == [paragraphs[8][0], paragraphs[9][0]]


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
            return '{"intent":"OTHER","icap":"passive"}', 10, 10
        calls.append((sys, user))
        if "【当前版教材目录】" in sys:
            assert all(title in sys for title in HEADINGS)
            if user.endswith("简单说说数组"):
                return '{"before":"数组用同一个名字存放多个同类型的值。"}', 10, 10
            assert "本轮先根据最近的对话与当前提问查目录" in sys
            if user.endswith("教材哪里讲了 Rust？"):
                return '{"textbookSearch":{"sections":[]},"before":"这本教材讲的是 C 语言。"}', 10, 10
            if user.endswith("怎么用"):
                assert "学生：啥叫 array index 呀？" in user
                return '{"textbookSearch":{"sections":[2],"keywords":""}}', 10, 10
            return '{"textbookSearch":{"sections":[2,999,3,2],"keywords":"array index length"}}', 10, 10
        return json.dumps({"before": "数组首元素的下标为 0。", "textbook": ["[t1]", "T999", "T1"]},
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
            for question in ("什么是 array index？", "array index 是啥", "啥叫 array index 呀？"):
                calls.clear()
                turn = client.post("/api/threads/new/messages", json={"text": question}).json()
                assert len(calls) == 2 and "【当前版教材目录】" not in calls[1][0]
                assert SENTENCE not in calls[0][1] and SENTENCE in calls[1][1]
                assert "The array length counts its elements." in calls[1][1]
                msg = turn["messages"][-1]
                assert msg["textbook"] == [{"edition": edition, "section": 2, "anchor": target["id"],
                                           "path": list(HEADINGS[:2])}]
                assert SENTENCE not in json.dumps(msg)
            history = client.get(f"/api/threads/{turn['thread']}").json()["messages"]
            assert history[-1]["textbook"] == msg["textbook"] and history[-1]["time"] == msg["time"]
            calls.clear()
            followup = client.post(f"/api/threads/{turn['thread']}/messages", json={"text": "怎么用"})
            assert followup.status_code == 200 and len(calls) == 2 and SENTENCE in calls[1][1]
            assert followup.json()["messages"][-1]["textbook"] == msg["textbook"]
            with db.session_scope() as s:
                step = s.query(m.ChatStep).order_by(m.ChatStep.id.desc()).first()
                assert step.check_result["textbookLookup"] == {
                    "available": True, "searched": True, "candidates": 1, "cited": 1}
            calls.clear()
            direct = client.post("/api/threads/new/messages", json={"text": "简单说说数组"}).json()
            assert len(calls) == 1 and not direct["messages"][-1].get("textbook")
            calls.clear()
            no_match = client.post("/api/threads/new/messages", json={"text": "教材哪里讲了 Rust？"}).json()
            assert len(calls) == 1 and not no_match["messages"][-1].get("textbook")
        finally:
            llm.set_backend(None)

        with db.session_scope() as s:
            learner = s.scalar(m.User.__table__.select().with_only_columns(m.User.id).where(
                m.User.student_id == "2500000001"))
        assert learner is not None
        app.dependency_overrides[student] = lambda: m.User(id=learner, role="student", course="D")
        assert client.get("/api/textbook/contents").json()["book"] is None
        assert client.get(prefix).status_code == 404 and client.get(image).status_code == 404
