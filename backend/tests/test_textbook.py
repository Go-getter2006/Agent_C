from __future__ import annotations

import json
import io
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import app.textbook_word as textbook_word
from app import db, llm, textbook
from app import models as m
from app.textbook_word import prepare
from app.web.app import create_app
from app.web.deps import student
from tests.test_pdftext import make_pdf

SENTENCE = "An array index starts at zero."
HEADINGS = ("1.1 Arrays", "1.1.1 Array index", "1.1.2 Array length")


def test_figure_matches_selected_id_or_an_exact_reference_in_the_text(demo):
    image = Image.new("L", (300, 240), 255)
    ImageDraw.Draw(image).rectangle((70, 110, 210, 145), outline=0)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    marks = [[0, .1, .11, .04, .004], [0, .14, .1, .4, .04]]
    anchors = [
        {"id": 1, "text": "一个指针也能指向指针变量。", "boxes": marks},
        {"id": 2, "text": "两个变量的指向关系如图 ６．２１ 所示。", "boxes": [[0, .2, .36, .5, .04]]},
        {"id": 3, "text": "图6.21 指向关系示意图", "boxes": [[0, .33, .65, .35, .04]]},
        {"id": 4, "text": "指针的声明格式如下：", "boxes": [[0, .2, .8, .5, .04]]},
        {"id": 5, "text": "<类型> *<指针变量名>", "boxes": [[0, .2, .86, .4, .045]]},
    ]
    with db.session_scope() as s:
        edition = m.TextbookEdition(title="测试", digest="figure-test")
        s.add(edition)
        s.flush()
        row = m.TextbookSection(edition_id=edition.id, number=1, title="指向指针的指针",
                                pages=[{"image": "f", "width": 300, "height": 240}], anchors=anchors)
        s.add_all([row, m.TextbookFigure(edition_id=edition.id, digest="f", png=buf.getvalue())])
        s.flush()
        choices = textbook.retrieve(s, "C", "指针的指针是啥", [], textbook.directory(s, "C"),
                                    textbook.Lookup((1,), "指针"))
        prompt = textbook.prompt(choices)
        assert "[F1]" in prompt and anchors[1]["text"] in prompt and anchors[2]["text"] in prompt
        key = next(k for k, (_, ref, _) in choices.items() if ref["anchor"] == 1)
        choice = {"id": key, "role": "key", "explanation": "沿箭头读出指向关系。"}
        plain, illustrated = [textbook.citations(choices, [c])[0] for c in (
            choice, dict(choice, figureId="F1", excerpt=[1, 3]))]
        assert illustrated["excerpt"] == [1, 3]
        linked = {"edition": edition.id, "section": 1, "anchor": 2}
        format_intro = {"edition": edition.id, "section": 1, "anchor": 4}
        textbook.add_previews(s, [plain, illustrated, linked, format_intro])
        assert len(plain["preview"]["boxes"]) == 1 and "frame" not in plain["preview"]
        x, y, w, h = illustrated["preview"]["frame"]
        assert x < 70 / 300 < 210 / 300 < x + w and y < 110 / 240 < .69 < y + h
        top, bottom = illustrated["preview"]["crop"]
        assert 0 < top < .1 * 240 < .69 * 240 < bottom < 240
        assert linked["figureAnchor"] == 3 and linked["preview"]["frame"] == [x, y, w, h]
        assert linked["preview"]["crop"][1] >= bottom
        assert format_intro["preview"]["crop"][1] >= .905 * 240
        edition_id = edition.id

    with TestClient(create_app()) as client:
        assert client.post("/api/auth/login", json={"loginName": "2500000001", "password": "test-pass"}).status_code == 200
        detail = client.get(f"/api/textbook/{edition_id}/sections/1", params={"anchor": 2})
        assert detail.status_code == 200
        assert detail.json()["highlight"]["frame"] == [0, x, y, w, h]


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
            if user.endswith("你好"):
                return '{"textbookSearch":{"sections":[],"reason":"问候"},"before":"你好！今天想聊点什么？😊"}', 10, 10
            if user.endswith("早上好"):
                return '{"before":"早上好！","textbookSearch":{"sections":[999]}}', 10, 10
            if user.endswith("谢谢，我懂了"):
                return '{"textbookSearch":{"sections":[],"reason":"学生在确认理解"},"before":"好。"}', 10, 10
            if user.endswith("看一下 array index") and len(messages) == 2:
                return '{"textbookSearch":{"sections":[999]}}', 10, 10
            if user.endswith("请解释 array index"):
                if len(messages) == 2:
                    return '{"before":"数组首元素的下标为 0。"}', 10, 10
                return '{"textbookSearch":{"sections":["2"],"keywords":"array index"}}', 10, 10
            assert "优先选到最小小节" in sys
            if user.endswith("教材哪里讲了 Rust？"):
                return '{"textbookSearch":{"sections":[],"reason":"无 Rust 章节"},"before":"这本教材讲的是 C 语言。"}', 10, 10
            if user.endswith("怎么用"):
                assert "学生：啥叫 array index 呀？" in user
                return '{"textbookSearch":{"sections":[2],"keywords":""}}', 10, 10
            return '{"textbookSearch":{"sections":[2,999,3,2],"keywords":"array index length"}}', 10, 10
        if user.endswith("换一种说法解释数组下标"):
            return '{"before":"第一个元素从位置 0 开始数。","textbook":[{"id":"未知编号"}]}', 10, 10
        if user.endswith("数组下标怎么取") and len(messages) == 2:
            return '{"before":"数组首元素的下标为 0。"}', 10, 10
        return json.dumps({"before": "数组首元素的下标为 0。", "textbook": [
            {"id": "[t1]", "role": "key", "explanation": "读第一个元素时，下标应写 0。"}]},
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
        highlight = client.get(prefix, params={"anchor": target["id"]}).json()["highlight"]
        assert highlight["text"] == SENTENCE
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
                ref, = msg["textbook"]
                preview = ref["preview"]
                assert {k: v for k, v in ref.items() if k != "preview"} == {
                    "edition": edition, "section": 2, "anchor": target["id"], "path": list(HEADINGS[:2]),
                    "role": "key", "explanation": "读第一个元素时，下标应写 0。"}
                assert preview["boxes"] == [box[1:] for box in highlight["boxes"]]
                top, bottom = preview["crop"]
                preview_png = client.get(image, params={"top": top, "bottom": bottom})
                with Image.open(io.BytesIO(preview_png.content)) as crop:
                    assert crop.size == (section.pages[0]["width"], bottom - top)
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
            greeting = client.post("/api/threads/new/messages", json={"text": "你好"})
            assert greeting.status_code == 200 and len(calls) == 1
            hello = greeting.json()["messages"][-1]
            assert hello["blocks"][0]["text"] == "你好！今天想聊点什么？😊" and not hello.get("textbook")
            calls.clear()
            good_morning = client.post("/api/threads/new/messages", json={"text": "早上好"})
            assert good_morning.status_code == 200 and len(calls) == 2
            assert good_morning.json()["messages"][-1]["blocks"][0]["text"] == "早上好！"
            with db.session_scope() as s:
                step = s.query(m.ChatStep).order_by(m.ChatStep.id.desc()).first()
                assert step.check_result["textbookLookup"]["reason"] == "模型未提供有效的查书决定"
            calls.clear()
            direct = client.post("/api/threads/new/messages", json={"text": "谢谢，我懂了"}).json()
            assert len(calls) == 1 and not direct["messages"][-1].get("textbook")
            calls.clear()
            no_match = client.post("/api/threads/new/messages", json={"text": "教材哪里讲了 Rust？"}).json()
            assert len(calls) == 1 and not no_match["messages"][-1].get("textbook")
            calls.clear()
            repaired = client.post("/api/threads/new/messages", json={"text": "看一下 array index"}).json()
            assert len(calls) == 3 and repaired["messages"][-1]["textbook"]
            for question in ("请解释 array index", "数组下标怎么取"):
                calls.clear()
                repaired = client.post("/api/threads/new/messages", json={"text": question})
                assert repaired.status_code == 200 and len(calls) == 3
                assert repaired.json()["messages"][-1]["textbook"] == msg["textbook"]
            calls.clear()
            partial = client.post("/api/threads/new/messages", json={"text": "换一种说法解释数组下标"})
            assert partial.status_code == 200 and len(calls) == 3
            partial_msg = partial.json()["messages"][-1]
            assert partial_msg["blocks"][0]["text"] == "第一个元素从位置 0 开始数。"
            assert not partial_msg.get("textbook")
            with db.session_scope() as s:
                old = s.get(m.ChatMessage, msg["id"])
                old.content = {**old.content, "textbook": [{k: ref[k] for k in ("edition", "section", "anchor", "path")}]}
            old = next(item for item in client.get(f"/api/threads/{turn['thread']}").json()["messages"] if item["id"] == msg["id"])
            assert old["textbook"][0]["preview"] == ref["preview"] and "explanation" not in old["textbook"][0]
        finally:
            llm.set_backend(None)

        with db.session_scope() as s:
            learner = s.scalar(m.User.__table__.select().with_only_columns(m.User.id).where(
                m.User.student_id == "2500000001"))
        assert learner is not None
        app.dependency_overrides[student] = lambda: m.User(id=learner, role="student", course="D")
        assert client.get("/api/textbook/contents").json()["book"] is None
        assert client.get(prefix).status_code == 404 and client.get(image).status_code == 404
