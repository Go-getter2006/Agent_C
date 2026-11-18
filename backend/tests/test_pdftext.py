"""题面识别：PDF 不直接复制文字（会乱码），用 OCR；乱码不给学生和大模型看。"""
from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app import db, pdftext
from app import models as m
from app.xdoj import sync_meta

STATEMENT = "输入一个正整数 n，输出它各位数字的和。"


def _pdf_with_text(text: str) -> bytes:
    """最小的 PDF：一页、Helvetica 字体、一行文字（有文字层）。"""
    content = f"BT /F1 16 Tf 30 100 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _image_pdf() -> bytes:
    """只有图片、没有文字层的 PDF（扫描件那样）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 160), "white")
    ImageDraw.Draw(img).text((20, 60), "a + b", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PDF")
    return buf.getvalue()


def _ocr_returning(text: str, seen: list | None = None):
    """假的 OCR：每一行文字一个框。"""
    def fn(img):
        if seen is not None:
            seen.append(img.size)
        return [([[10, 10 + 30 * i], [300, 10 + 30 * i], [300, 34 + 30 * i], [10, 34 + 30 * i]], line, 0.95)
                for i, line in enumerate(text.split("\n"))]
    return fn


@pytest.fixture()
def ocr():
    """装一个假的 OCR；测完恢复。"""
    def install(text: str, seen: list | None = None):
        pdftext.set_ocr(_ocr_returning(text, seen))
    yield install
    pdftext.set_ocr(None)


@pytest.fixture()
def no_ocr(monkeypatch):
    monkeypatch.setattr(pdftext, "_ocr_fn", lambda: None)
    monkeypatch.setattr(pdftext, "ocr_available", lambda: False)


def test_garbled_text_is_recognized():
    assert pdftext.garbled(STATEMENT + "输入格式：一行一个整数。") is None
    assert pdftext.garbled("Input two integers a and b, output a+b.") is None
    for bad in ("(cid:3)(cid:45)(cid:88)(cid:12) n (cid:9)",              # 字体没有文字对照表
                "杈撳叆涓€涓鏁存暟n锛岃緭鍑哄畠鍚勪綅鏁板瓧鐨勫拰",            # 字形对应错，全是生僻字
                "è¾“å…¥ä¸€ä¸ªæ­£æ•´æ•°nï¼Œè¾“å‡ºå®ƒå„ä½",                  # 编码弄错
                " n "):    # 私用区
        assert pdftext.garbled(bad), bad


def test_ocr_boxes_become_lines():
    items = [([[120, 10], [200, 10], [200, 30], [120, 30]], "输出", 0.9),
             ([[10, 12], [100, 12], [100, 32], [10, 32]], "输入", 0.9),         # 和上一个在同一行，在左边
             ([[10, 40], [200, 40], [200, 60], [10, 60]], "第二行", 0.9),
             ([[10, 140], [200, 140], [200, 160], [10, 160]], "隔了一段", 0.9),
             ([[10, 170], [200, 170], [200, 190], [10, 190]], "看不清", 0.2)]    # 置信度太低，不要
    assert pdftext.lines_from_boxes(items) == "输入 输出\n第二行\n\n隔了一段"


def test_clean_layer_that_matches_ocr_is_used(ocr):
    ocr("Input two integers a and b")
    text, status = pdftext.extract("application/pdf", _pdf_with_text("Input two integers a and b"))
    assert status == "verified" and "Input two integers" in text


def test_garbled_layer_is_replaced_by_ocr(ocr, monkeypatch):
    monkeypatch.setattr(pdftext, "layer_text", lambda data: "杈撳叆涓€涓鏁存暟n锛岃緭鍑哄畠鍚勪綅鏁板瓧鐨勫拰")
    ocr(STATEMENT)
    assert pdftext.extract("application/pdf", _pdf_with_text("x")) == (STATEMENT, "ocr")


def test_layer_that_disagrees_with_ocr_loses(ocr, monkeypatch):
    """乱码也可能“看起来正常”：和 OCR 对不上就不用文字层。"""
    monkeypatch.setattr(pdftext, "layer_text", lambda data: "输出两个数中较大的那一个，然后换行结束程序。")
    ocr(STATEMENT)
    assert pdftext.extract("application/pdf", _pdf_with_text("x")) == (STATEMENT, "ocr")


def test_scanned_pdf_is_rendered_and_recognized(ocr):
    seen: list = []
    ocr(STATEMENT, seen)
    text, status = pdftext.extract("application/pdf", _image_pdf())
    assert (text, status) == (STATEMENT, "ocr")
    assert seen and seen[0][0] > 480        # 真的渲染了这一页（放大了）


def test_without_ocr_garbled_text_is_never_shown(no_ocr, monkeypatch):
    monkeypatch.setattr(pdftext, "layer_text", lambda data: "(cid:3)(cid:45)(cid:88)(cid:12)(cid:9)")
    text, status = pdftext.extract("application/pdf", b"%PDF-1.4 ...")
    assert (text, status) == ("", "needs_ocr")
    monkeypatch.setattr(pdftext, "layer_text", lambda data: STATEMENT)
    assert pdftext.extract("application/pdf", b"%PDF-1.4 ...") == (STATEMENT, "unverified")


def test_sync_stores_recognized_statement(ctx, fake, ocr):
    fake.problem_files[406] = (_image_pdf(), "application/pdf")
    ocr(STATEMENT)
    sync_meta.sync_metadata(ctx)
    sync_meta.sync_problem_assets(ctx, 406)
    with db.session_scope() as s:
        row = s.get(m.ProblemText, (ctx.server_id, 406))
        assert row.extract_status == "ocr" and pdftext.usable_text(row) == STATEMENT
        assert row.file_bytes[:5] == b"%PDF-"                       # 原文件留着，以后能重新识别


def test_old_statements_are_recognized_again(ctx, fake, ocr):
    """以前直接抽文字存下的 PDF 题面（乱码，状态还是 ok）：空闲时自动重新识别，并重新标注。"""
    with db.session_scope() as s:
        s.add(m.ProblemText(server_id=ctx.server_id, problem_id=4242, content_type="application/pdf",
                            file_bytes=_image_pdf(), file_hash="h", extracted_text="杈撳叆涓€涓鏁存暟",
                            extract_status="ok"))
    ocr(STATEMENT)
    with db.session_scope() as s:
        assert pdftext.redo_stored(s) == 1
    with db.session_scope() as s:
        row = s.get(m.ProblemText, (ctx.server_id, 4242))
        assert (row.extracted_text, row.extract_status) == (STATEMENT, "ocr")
        assert s.scalars(select(m.FetchTask).where(m.FetchTask.kind == "label_problem",
                                                   m.FetchTask.target_id == 4242)).first() is not None
        assert pdftext.redo_stored(s) == 0                           # 只做一次
