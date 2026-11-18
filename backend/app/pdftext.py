"""题面识别。XDOJ 的题面是 PDF（`/problem/viewTextFile.do`），不能直接复制里面的文字：

很多 PDF 嵌的是子集字体、没有“字形 → 文字”的对照表，直接抽出来是乱码——生僻字堆在一起、`(cid:123)`、
私用区字符，或者 UTF-8 被当成 Latin-1 的“è¾“å…¥”。而且乱码不报错，以前程序看抽出来“有字”就当成功了。

现在的做法：
1. 每一页渲染成图片（pypdfium2，pdfplumber 本来就依赖它），用 OCR 识别（RapidOCR，离线、认中文）。
2. PDF 自带的文字层也抽一份，先检查像不像乱码（`garbled`）。
3. 文字层干净、而且和 OCR 的结果对得上 → 用文字层（符号、代码比 OCR 准），状态 verified；
   否则用 OCR 的结果，状态 ocr。
4. 服务器上没装 OCR：文字层干净就先用（unverified，装上 OCR 后自动重新识别）；是乱码就不用（needs_ocr），
   宁可没有题面，也不给学生和大模型看乱码。

能用的状态是 USABLE；其余状态的题面文字一律当作没有。
"""
from __future__ import annotations

import difflib
import io
import logging
import re
import threading
import time
import unicodedata
from typing import Callable

log = logging.getLogger("pdftext")

USABLE = frozenset({"ok", "verified", "ocr", "unverified"})
STATUS_NAMES = {
    "ok": "文字文件", "verified": "PDF 文字层（和 OCR 核对过）", "ocr": "OCR 识别", "unverified": "PDF 文字层（没有 OCR 核对）",
    "needs_ocr": "PDF 文字层是乱码，等 OCR", "unreadable": "PDF 打不开", "failed": "没有内容",
}
# 以前的程序对 PDF 也写 ok / empty_needs_ocr / failed：这些 PDF 要用现在的办法重新识别一遍
_OLD_PDF_STATUS = frozenset({"ok", "empty_needs_ocr", "failed"})

MAX_PAGES = 4
RENDER_SCALE = 2.0  # 144 dpi，题面的小字也认得清
MIN_SCORE = 0.5
AGREE = 0.6  # 文字层和 OCR 的相似度到这个数才算对得上


def is_pdf(content_type: str, data: bytes | None) -> bool:
    return bool(data) and (data[:5] == b"%PDF-" or "pdf" in (content_type or "").lower())


# ------------------------------------------------------------ 乱码检查
_CID = re.compile(r"\(cid:\d+\)")


def _common_cjk(ch: str) -> bool | None:
    """常用汉字（GB2312 一级字，3755 个）返回 True，其他汉字 False，不是汉字 None。"""
    if not ("一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"):
        return None
    try:
        b = ch.encode("gb2312")
    except UnicodeEncodeError:
        return False
    return 0xB0 <= b[0] <= 0xD7


def garbled(text: str) -> str | None:
    """像不像乱码。像的话返回原因，不像返回 None。题面是给学生的中文说明，正常的几乎全是常用字。"""
    body = "".join(ch for ch in (text or "") if not ch.isspace())
    if len(body) < 8:
        return "几乎没有文字"
    if _CID.search(text):
        return "有 (cid:…) 占位符：字体没有文字对照表"
    n = len(body)
    bad = sum(1 for ch in body if ch == "�" or "" <= ch <= ""
              or (unicodedata.category(ch) in ("Cc", "Co", "Cs", "Cn")))
    if bad / n > 0.02:
        return "有很多无法显示的字符"
    cjk = [c for c in (_common_cjk(ch) for ch in body) if c is not None]
    if len(cjk) >= 10 and sum(cjk) / len(cjk) < 0.9:
        return "生僻字太多：字形对应错了"
    latin1 = sum(1 for ch in body if "À" <= ch <= "ÿ")
    letters = sum(1 for ch in body if ch.isalpha())
    if letters and latin1 / letters > 0.1:
        return "像是编码弄错了（UTF-8 当成了别的编码）"
    return None


def _norm(text: str) -> str:
    """比较用：只留汉字、字母、数字和常见符号，去掉空白。"""
    return "".join(ch.lower() for ch in text if ch.isalnum() or ch in "+-*/%=<>()[]{}\"'.,;:!?，。；：！？（）")


def similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


# ------------------------------------------------------------ OCR
OcrFn = Callable[[object], list[tuple[list, str, float]]]  # 图片 → [(四个角的坐标, 文字, 置信度)]
_ocr_override: OcrFn | None = None
_engine = None
_engine_failed_at = 0.0
_lock = threading.Lock()


def set_ocr(fn: OcrFn | None) -> None:
    """测试用：换一个识别函数；None 恢复默认。"""
    global _ocr_override
    _ocr_override = fn


def _load_engine():
    """RapidOCR：新版（rapidocr，3.x）和旧版（rapidocr_onnxruntime，1.x）都认。装不上就返回 None，一小时后再试。"""
    global _engine, _engine_failed_at
    if _engine is not None:
        return _engine
    if _engine_failed_at and time.monotonic() - _engine_failed_at < 3600:
        return None
    try:
        try:
            from rapidocr import RapidOCR  # type: ignore[import-not-found]

            _engine = ("v3", RapidOCR())
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

            _engine = ("v1", RapidOCR())
        log.info("OCR 可以用了（%s）", _engine[0])
    except Exception as exc:  # 没装、模型没下载下来……都当作没有 OCR
        _engine_failed_at = time.monotonic()
        log.warning("没有可用的 OCR（%s）：PDF 题面先用文字层，乱码的等装上 OCR 再识别", type(exc).__name__)
        return None
    return _engine


def _rapid(img) -> list[tuple[list, str, float]]:
    import numpy as np  # RapidOCR 自带的依赖

    kind, engine = _engine
    arr = np.asarray(img.convert("RGB"))
    if kind == "v3":
        out = engine(arr)
        boxes, txts, scores = getattr(out, "boxes", None), getattr(out, "txts", None), getattr(out, "scores", None)
        if boxes is None or txts is None:
            return []
        scores = scores if scores is not None else [1.0] * len(txts)
        return [(b.tolist() if hasattr(b, "tolist") else list(b), str(t), float(sc)) for b, t, sc in zip(boxes, txts, scores)]
    result, _elapse = engine(arr)
    return [(list(r[0]), str(r[1]), float(r[2])) for r in (result or [])]


def ocr_available() -> bool:
    if _ocr_override is not None:
        return True
    with _lock:
        return _load_engine() is not None


def _ocr_fn() -> OcrFn | None:
    if _ocr_override is not None:
        return _ocr_override
    with _lock:
        return _rapid if _load_engine() is not None else None


def render_pages(data: bytes, max_pages: int = MAX_PAGES, scale: float = RENDER_SCALE) -> list:
    """PDF 的前几页渲染成图片（PIL）。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return [pdf[i].render(scale=scale).to_pil() for i in range(min(len(pdf), max_pages))]
    finally:
        pdf.close()


def lines_from_boxes(items: list[tuple[list, str, float]]) -> str:
    """OCR 的一个个文字块拼成一行行：上下重叠一半以上的算同一行，同一行从左往右；行距大的地方空一行。"""
    blocks = []
    for box, text, score in items:
        if score < MIN_SCORE or not str(text).strip():
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        blocks.append((min(xs), max(xs), min(ys), max(ys), str(text).strip()))
    blocks.sort(key=lambda b: ((b[2] + b[3]) / 2, b[0]))
    lines: list[list[tuple]] = []
    for b in blocks:
        if lines:
            top = min(x[2] for x in lines[-1])
            bottom = max(x[3] for x in lines[-1])
            overlap = min(bottom, b[3]) - max(top, b[2])
            if overlap > 0.5 * min(bottom - top, b[3] - b[2]):
                lines[-1].append(b)
                continue
        lines.append([b])
    out: list[str] = []
    prev_bottom = None
    for line in lines:
        line.sort(key=lambda b: b[0])
        height = sum(b[3] - b[2] for b in line) / len(line)
        top = min(b[2] for b in line)
        if prev_bottom is not None and top - prev_bottom > 1.2 * height:
            out.append("")
        text = line[0][4]
        for a, b in zip(line, line[1:]):
            gap = b[0] - a[1]
            text += (" " if gap > 0.6 * height else "") + b[4]
        out.append(text)
        prev_bottom = max(b[3] for b in line)
    return "\n".join(out).strip()


def ocr_pdf(data: bytes) -> str | None:
    """OCR 识别整份 PDF。没有 OCR 返回 None；识别出错也返回 None（记日志，不记内容）。"""
    fn = _ocr_fn()
    if fn is None:
        return None
    try:
        pages = render_pages(data)
        return "\n\n".join(lines_from_boxes(fn(img)) for img in pages).strip()
    except Exception as exc:
        log.warning("OCR 识别出错：%s", type(exc).__name__)
        return None


def layer_text(data: bytes) -> str | None:
    """PDF 自带的文字层。打不开返回 None。"""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages[:MAX_PAGES]).strip()
    except Exception:
        return None


# ------------------------------------------------------------ 对外
def _plain(data: bytes) -> tuple[str, str]:
    from .xdoj.util import decode_bytes, detect_encoding

    text, ok = decode_bytes(data, detect_encoding(data))
    if not ok or not (text or "").strip():
        return "", "failed"
    if "<html" in text.lower() or "<body" in text.lower() or "<p" in text.lower():
        text = re.sub(r"<(script|style)[\s\S]*?</\1>", "", text, flags=re.I)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
    text = text.strip()
    return (text, "ok") if garbled(text) is None else ("", "failed")


def extract(content_type: str, data: bytes) -> tuple[str, str]:
    """题面文件 → (文字, 状态)。状态在 USABLE 里的文字才能用。"""
    if not data:
        return "", "failed"
    if not is_pdf(content_type, data):
        return _plain(data)
    layer = layer_text(data)
    ocr = ocr_pdf(data)
    if layer is None and not ocr:
        return "", "unreadable"
    layer_bad = garbled(layer or "")
    if ocr:
        if layer_bad is None and similarity(layer or "", ocr) >= AGREE:
            return layer or "", "verified"
        if layer_bad is not None:
            log.info("PDF 文字层不能用（%s），用 OCR 的结果", layer_bad)
        return ocr, "ocr"
    if layer_bad is None:
        return layer or "", "unverified"
    log.info("PDF 文字层不能用（%s），没有 OCR，先不显示题面", layer_bad)
    return "", "needs_ocr"


def needs_redo(status: str | None, file_bytes: bytes | None, content_type: str = "") -> bool:
    """库里已有的题面要不要重新识别：以前的程序处理的 PDF；或者当时没有 OCR、现在有了。"""
    if not is_pdf(content_type, file_bytes):
        return False
    if status in _OLD_PDF_STATUS:
        return True
    return status in ("needs_ocr", "unverified") and ocr_available()


def usable_text(row) -> str:
    """题面文字（ProblemText 行）；乱码、没识别出来的返回空串。"""
    if row is None or (row.extract_status or "") not in USABLE:
        return ""
    return row.extracted_text or ""


def redo_stored(s, limit: int = 1) -> int:
    """把库里需要重新识别的题面识别一遍（不用再向 XDOJ 请求：文件本身存着）。文字变了就重新标注这道题。

    后台空闲时自动做，每次一道（OCR 要几秒），不占着同步。返回处理了几道。
    """
    from sqlalchemy import func, select

    from . import models as m
    from .xdoj.store import enqueue

    T = m.ProblemText
    cands = s.execute(select(T.server_id, T.problem_id, T.extract_status, T.content_type, func.substr(T.file_bytes, 1, 5))
                      .where(T.extract_status.in_(sorted(_OLD_PDF_STATUS | {"needs_ocr", "unverified"})))
                      .order_by(T.server_id, T.problem_id)).all()
    done = 0
    for server_id, problem_id, status, ctype, head in cands:
        if done >= limit:
            break
        if not needs_redo(status, bytes(head or b""), ctype or ""):  # 只读了前 5 个字节，够判断是不是 PDF
            continue
        row = s.get(T, (server_id, problem_id))
        text, new_status = extract(row.content_type, row.file_bytes)
        changed = text != (row.extracted_text or "")
        row.extracted_text, row.extract_status = text, new_status
        if changed and new_status in USABLE:
            enqueue(s, server_id, "label_problem", problem_id, 2)
        log.info("重新识别题目 %s 的题面：%s", problem_id, STATUS_NAMES.get(new_status, new_status))
        done += 1
    return done
