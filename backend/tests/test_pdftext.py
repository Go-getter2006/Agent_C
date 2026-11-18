"""题面整理：不用 OCR。PDF 文字层抽文字 + 示意图裁成 PNG + 大模型整理（程序核对）；乱码不给学生和大模型看。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, llm, pdftext
from app import models as m
from app.web.app import create_app
from app.xdoj import sync_meta

STATEMENT = "输入一个正整数 n，输出它各位数字的和。"


def make_pdf(lines: list[str], image: bool = False, drawing: bool = False, sample_box: bool = False) -> bytes:
    """最小的 PDF：一页，Helvetica 写几行字；可以加一张图片、一块画出来的示意图、一个框住样例的方框。"""
    ops = [b"BT /F1 12 Tf 30 270 Td 14 TL"] + [b"(" + ln.encode("latin-1") + b") Tj T*" for ln in lines] + [b"ET"]
    if image:
        ops.append(b"q 120 0 0 60 40 120 cm /Im1 Do Q")
    if drawing:
        ops.append(b"0.5 w 220 120 m 360 180 l S 220 120 140 60 re S 230 125 m 260 170 300 170 340 125 c S")
    if sample_box:   # 样例外面的方框：里面全是字，不算示意图
        ops.append(b"25 228 200 46 re S")
    content = b"\n".join(ops)
    img = bytes((x * 16) % 256 for x in range(16 * 8))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 300] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /XObject /Subtype /Image /Width 16 /Height 8 /ColorSpace /DeviceGray /BitsPerComponent 8 "
            b"/Length %d >>\nstream\n" % len(img) + img + b"\nendstream"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


LINES = ["Input two integers a and b (1 <= a, b <= 1000).", "Output a+b.", "Sample input", "3 5", "Sample output", "8"]


@pytest.fixture()
def fake_llm():
    """假的大模型：依次按给定的 JSON 回答（最后一个一直重复），记下收到的提示词。"""
    seen: list[str] = []

    def install(*answers: dict):
        queue = list(answers)

        def backend(messages, json_mode, max_tokens, temperature):
            seen.append(messages[1]["content"])
            return json.dumps(queue.pop(0) if len(queue) > 1 else queue[0], ensure_ascii=False), 100, 50
        llm.set_backend(backend)
        return seen
    yield install
    llm.set_backend(None)


# ------------------------------------------------------------ 抽文字
def test_garbled_text_is_recognized():
    assert pdftext.garbled(STATEMENT + "输入格式：一行一个整数。") is None
    assert pdftext.garbled("Input two integers a and b, output a+b.") is None
    for bad in ("(cid:3)(cid:45)(cid:88)(cid:12) n (cid:9)",             # 字体没有文字对照表
                "杈撳叆涓€涓鏁存暟n锛岃緭鍑哄畠鍚勪綅鏁板瓧鐨勫拰",           # 字形对应错，全是生僻字
                "\ue001\ue002\ue003\ue004 n \ue005\ue006\ue007\ue008"):   # 私用区
        assert pdftext.garbled(bad), bad


def test_wrong_encoding_is_reversed_by_program():
    """UTF-8、GBK 的字节被当成 Latin-1 解：程序能还原，不用大模型。"""
    assert pdftext.fix_mojibake(STATEMENT.encode("utf-8").decode("latin-1")) == STATEMENT
    assert pdftext.fix_mojibake(STATEMENT.encode("gbk").decode("latin-1")) == STATEMENT
    assert pdftext.fix_mojibake(STATEMENT) == STATEMENT


def test_spaces_between_chinese_characters_are_removed():
    assert pdftext._tidy_spaces("输 入 一 个 整 数 n ，输出 a + b 。") == "输入一个整数 n，输出 a + b。"


def test_text_layer_and_figures():
    text, status, figs = pdftext.extract("application/pdf", make_pdf(LINES, image=True, drawing=True, sample_box=True))
    assert status == "layer"
    assert text.split("\n")[:6] == LINES                                  # 字照原样、按行
    assert text.endswith("[图1]\n[图2]") and len(figs) == 2               # 图片、画的示意图；样例框不算
    assert all(f["png"][:8] == b"\x89PNG\r\n\x1a\n" and f["width"] > 100 for f in figs)


def test_private_arrow_is_repaired_only_for_known_font():
    text = "字符转换：a\uf0e8d，B\uf0e8Z"
    table = pdftext._symbol_translation([{"text": "\uf0e8", "fontname": "ABCDEE+Wingdings"}])
    assert text.translate(table) == "字符转换：a→d，B→Z"
    assert pdftext.garbled(text.translate(table)) is None
    for font in ("OtherFont", "Wingdings2", "Wingdings3"):
        assert not pdftext._symbol_translation([{"text": "\uf0e8", "fontname": font}])
    assert not pdftext._symbol_translation([{"text": "\uf0e8", "fontname": "Wingdings"},
                                          {"text": "\uf0e8", "fontname": "OtherFont"}])


def test_cached_arrow_statement_is_repaired_without_network(ctx, monkeypatch):
    old = "字符转换：a\uf0e8d，B\uf0e8Z"
    fixed = old.replace("\uf0e8", "→")
    with db.session_scope() as s:
        s.add(m.ProblemText(server_id=ctx.server_id, problem_id=4242, content_type="application/pdf",
                            file_bytes=make_pdf(LINES), raw_text=old, extracted_text="", extract_status="garbled",
                            tidied_at=db.now()))
    monkeypatch.setattr(pdftext, "read_pdf", lambda data: (fixed, []))
    with db.session_scope() as s:
        assert pdftext.redo_stored(s) == 1
        assert pdftext.usable_text(s.get(m.ProblemText, (ctx.server_id, 4242))) == fixed
        assert pdftext.redo_stored(s) == 0


# ------------------------------------------------------------ 大模型整理：程序核对
RAW = "Input two integers a and b (1 <= a, b <= 10 5).\n[图1]\nSample input\n3 5\nSample output\n8"
GOOD = "题目描述\nInput two integers a and b (1 ≤ a, b ≤ 10^5).\n[图1]\n样例输入\n3 5\n样例输出\n8"


def test_check_tidy():
    assert pdftext.check_tidy(RAW, GOOD, raw_garbled=False) is None
    assert "数字" in pdftext.check_tidy(RAW, GOOD.replace("\n8", "\n9"), False)       # 改了样例
    assert "丢了" in pdftext.check_tidy(RAW, "Input two integers a and b.\n[图1]", False)
    assert "图" in pdftext.check_tidy(RAW, GOOD.replace("[图1]\n", ""), False)
    assert "内容" in pdftext.check_tidy("输入两个整数 a 和 b，输出它们的和 3 5 8",
                                       "输入两个整数 a 和 b，输出它们的和。注意要用循环累加求解 3 5 8", False)
    # 原文是乱码时，大模型还原出的汉字当然不在原文里：不按这一条卡，但数字照样核对
    assert pdftext.check_tidy("杈撳叆涓€涓鏁存暟 3 5 8", "输入一个正整数 3 5 8", raw_garbled=True) is None
    assert pdftext.check_tidy("杈撳叆涓€涓鏁存暟 3 5 8", "输入一个正整数 3 5 9", raw_garbled=True)


def test_tidy_falls_back_when_the_model_invents(fake_llm):
    seen = fake_llm({"text": GOOD, "restored": False})
    assert pdftext.tidy(RAW, "layer") == (GOOD, "llm")
    assert "【抽出来的题面】" in seen[0] and "[图1]" in seen[0]
    fake_llm({"text": GOOD.replace("\n8", "\n42"), "restored": False})      # 两次都编了数字
    assert pdftext.tidy(RAW, "layer") is None
    fake_llm({"text": "输入一个正整数 3 5 8 [图1] 10 5", "restored": True})
    assert pdftext.tidy("杈撳叆涓€涓鏁存暟 3 5 8 [图1] 10 5", "garbled") == ("输入一个正整数 3 5 8 [图1] 10 5", "llm_guess")


# ------------------------------------------------------------ 同步、存库、学生端
def _pdf_problem(ctx, fake, **kw):
    fake.problem_files[406] = (make_pdf(LINES, **kw), "application/pdf")
    sync_meta.sync_metadata(ctx)
    sync_meta.sync_problem_assets(ctx, 406)


def test_sync_stores_text_figures_and_tidies_once(ctx, fake, fake_llm):
    _pdf_problem(ctx, fake, image=True)
    with db.session_scope() as s:
        row = s.get(m.ProblemText, (ctx.server_id, 406))
        assert row.extract_status == "layer" and row.raw_text.startswith(LINES[0]) and row.tidied_at is None
        assert s.get(m.ProblemFigure, (ctx.server_id, 406, 1)) is not None
    tidied = "题目描述\n" + "\n".join(LINES) + "\n[图1]"
    fake_llm({"text": tidied, "restored": False})
    with db.session_scope() as s:
        assert pdftext.prepare(s, ctx.server_id, 406)
        row = s.get(m.ProblemText, (ctx.server_id, 406))
        assert (row.extract_status, pdftext.usable_text(row)) == ("llm", tidied)
        assert not pdftext.prepare(s, ctx.server_id, 406)                 # 每个文件只整理一次
        assert row.raw_text.startswith(LINES[0])                           # 原文留着


def test_without_llm_garbled_statement_is_not_shown(ctx, fake, monkeypatch):
    monkeypatch.setattr(pdftext, "read_pdf", lambda data: ("(cid:3)(cid:45)(cid:88)(cid:12)(cid:9)(cid:7)", []))
    _pdf_problem(ctx, fake)
    with db.session_scope() as s:
        row = s.get(m.ProblemText, (ctx.server_id, 406))
        assert row.extract_status == "garbled" and pdftext.usable_text(row) == ""
        assert not pdftext.prepare(s, ctx.server_id, 406)                 # 没有大模型：等以后


def test_old_statements_are_extracted_again(ctx, fake, fake_llm):
    """以前的程序存下的 PDF 题面（直接抽文字、OCR 那一版的状态）：空闲时自动重新抽、整理，并重新标注。"""
    with db.session_scope() as s:
        s.add(m.ProblemText(server_id=ctx.server_id, problem_id=4242, content_type="application/pdf",
                            file_bytes=make_pdf(LINES), file_hash="h", extracted_text="(cid:1)(cid:2)", extract_status="ok"))
        s.add(m.ProblemText(server_id=ctx.server_id, problem_id=4243, content_type="text/plain",
                            file_bytes=STATEMENT.encode("utf-8"), file_hash="h2", extracted_text=STATEMENT,
                            extract_status="ok"))
    fake_llm({"text": "题目描述\n" + "\n".join(LINES), "restored": False})
    with db.session_scope() as s:
        assert pdftext.redo_stored(s, limit=5) == 2                       # 重新抽一道，再整理这一道
    with db.session_scope() as s:
        assert s.get(m.ProblemText, (ctx.server_id, 4242)).extract_status == "llm"
        assert s.get(m.ProblemText, (ctx.server_id, 4243)).extract_status == "ok"     # 文字文件不动
        assert s.scalars(select(m.FetchTask).where(m.FetchTask.kind == "label_problem",
                                                   m.FetchTask.target_id == 4242)).first() is not None
        assert pdftext.redo_stored(s) == 0


def test_student_sees_the_statement_directly(demo, monkeypatch):
    """学生端直接显示题目：文字整理得出来就显示文字和示意图；整理不出来（乱码、没有大模型）就显示 PDF 的各页。
    不需要学生点开 PDF。"""
    sid = demo["server_id"]
    pdf = make_pdf(LINES, drawing=True)
    with db.session_scope() as s:
        row = s.get(m.ProblemText, (sid, 406))
        row.content_type, row.file_bytes, row.file_hash = "application/pdf", pdf, "x1"
        pdftext.store(s, row, "application/pdf", pdf)
    key = f"p{sid}-406"
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "2500000001", "password": "test-pass"})
        d = c.get(f"/api/problems/{key}").json()
        assert "[图1]" in d["statement"] and d["figures"][0]["n"] == 1 and d["pages"] == []
        fig = c.get(d["figures"][0]["url"])
        assert fig.status_code == 200 and fig.headers["content-type"] == "image/png"
        assert c.get(f"/api/problems/{key}/figures/9").status_code == 404
        assert c.get(f"/api/problems/{key}/original").status_code == 404     # 不再有“点开 PDF”

        monkeypatch.setattr(pdftext, "read_pdf", lambda data: ("(cid:3)(cid:45)(cid:88)(cid:12)(cid:9)(cid:7)", []))
        with db.session_scope() as s:
            row = s.get(m.ProblemText, (sid, 406))
            pdftext.store(s, row, "application/pdf", pdf)
        d = c.get(f"/api/problems/{key}").json()
        assert d["statement"] == "" and d["pages"] == [f"/api/problems/{key}/pages/1"]
        page = c.get(d["pages"][0])
        assert page.status_code == 200 and page.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert c.get(f"/api/problems/{key}/pages/2").status_code == 404
        assert c.get(f"/api/problems/p{sid}-999999/pages/1").status_code == 404     # 看不到的题
