"""题面整理。XDOJ 的题面是 PDF（`/problem/viewTextFile.do`），都带文字层，不是扫描件。**不用 OCR。**

直接复制 PDF 里的文字常常不对：多余的空格和断行、行的顺序乱、上标下标被拆开（10^5 变成“10 5”）、
≤ ≥ × 这类符号丢了，个别字体还会出乱码。DeepSeek 处理 PDF 也是先抽文字（它的接口只收文字），这里同样：

1. 抽文字：PDF 自带的文字层用两种办法各抽一遍（pdfplumber 按行、PDFium），挑乱码少的；
   编码错位（UTF-8 被当成 Latin-1 之类）程序能还原的先还原。
2. 示意图：PDF 里的图片、成片的线条（画出来的示意图）从页面渲染图上裁下来存成 PNG，文字里在那个位置写 [图1]。
   这只是把那块区域截下来给学生看，不识别图里的字。大模型看不到图，提示词里告诉它那是图。
3. 大模型整理（`tidy`）：修上面那些抽取问题，分出题目描述、输入、输出、样例；不许加原文没有的内容。
   程序核对：数字只能来自原文、原文的数字基本都在（样例不能丢）、[图N] 都在、汉字绝大多数来自原文（原文不是乱码时）、
   长度相当。不合格重写一次，还不行就用程序抽的文字。
4. 原文有认不出的乱码（字体没有文字对照表）时，大模型能按上下文还原，但没法核对，状态记 llm_guess；
   学生端照样显示。没有大模型时乱码的文字不显示，学生端直接显示 PDF 的页面（渲染成图，`page_png`）。

能用的状态是 USABLE；别的状态题面文字一律当作没有。
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata

log = logging.getLogger("pdftext")

USABLE = frozenset({"ok", "layer", "llm", "llm_guess"})
STATUS_NAMES = {
    "ok": "文字文件", "layer": "PDF 文字层（程序抽取）", "llm": "大模型整理过（已核对）",
    "llm_guess": "大模型还原了乱码（没法核对）", "garbled": "PDF 文字层是乱码，等大模型整理",
    "unreadable": "PDF 打不开", "failed": "没有内容",
}
# 现在的程序给 PDF 写的状态；不在这里面的（以前直接抽文字的 ok、OCR 那一版的状态……）都要重新抽一遍
_PDF_STATUS = frozenset({"layer", "llm", "llm_guess", "garbled", "unreadable"})

MAX_PAGES = 6
MAX_FIGURES = 8
RENDER_SCALE = 2.0  # 裁示意图用：144 dpi
FIG_MARK = re.compile(r"\[图(\d+)\]")


def is_pdf(content_type: str, data: bytes | None) -> bool:
    return bool(data) and (data[:5] == b"%PDF-" or "pdf" in (content_type or "").lower())


# ------------------------------------------------------------ 乱码检查
_CID = re.compile(r"\(cid:\d+\)")


def _common_cjk(ch: str) -> bool | None:
    """常用汉字（GB2312 一级字，3755 个）返回 True，其他汉字 False，不是汉字 None。"""
    if not ("\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf"):
        return None
    try:
        b = ch.encode("gb2312")
    except UnicodeEncodeError:
        return False
    return 0xB0 <= b[0] <= 0xD7


def _signals(text: str) -> dict[str, int]:
    """乱码的几种迹象各有多少。"""
    body = "".join(ch for ch in (text or "") if not ch.isspace())
    cjk = [c for c in (_common_cjk(ch) for ch in body) if c is not None]
    return {
        "n": len(body),
        "cid": len(_CID.findall(text or "")),
        "bad": sum(1 for ch in body if ch == "\ufffd" or "\ue000" <= ch <= "\uf8ff"
                   or unicodedata.category(ch) in ("Cc", "Co", "Cs", "Cn")),
        "cjk": len(cjk),
        "rare": len(cjk) - sum(cjk),
        "latin1": sum(1 for ch in body if "\u00c0" <= ch <= "\u00ff"),
        "letters": sum(1 for ch in body if ch.isalpha()),
    }


def garbled(text: str) -> str | None:
    """像不像乱码。像的话返回原因，不像返回 None。题面是给学生的中文说明，正常的几乎全是常用字。"""
    g = _signals(text)
    if g["n"] < 8:
        return "几乎没有文字"
    if g["cid"]:
        return "有 (cid:…) 占位符：字体没有文字对照表"
    if g["bad"] / g["n"] > 0.02:
        return "有很多无法显示的字符"
    if g["cjk"] >= 10 and g["rare"] / g["cjk"] > 0.1:
        return "生僻字太多：字形对应错了"
    if g["letters"] and g["latin1"] / g["letters"] > 0.1:
        return "像是编码弄错了（UTF-8 当成了别的编码）"
    return None


def badness(text: str) -> float:
    """乱码程度（越小越好），用来在两种抽法里挑一个。"""
    g = _signals(text)
    if g["n"] == 0:
        return float("inf")
    return (5 * g["cid"] + g["bad"] + (g["rare"] if g["cjk"] >= 10 else 0) + g["latin1"]) / g["n"]


def fix_mojibake(text: str) -> str:
    """编码错位：UTF-8 / GBK 的字节被当成 Latin-1 或 Windows-1252 解出来的（“è¾“å…¥”）。能还原就还原。"""
    if garbled(text) is None or not any("\u00c0" <= ch <= "\u00ff" for ch in text):
        return text
    for wrong in ("latin-1", "cp1252"):
        for right in ("utf-8", "gb18030"):
            try:
                fixed = text.encode(wrong).decode(right)
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if garbled(fixed) is None:
                return fixed
    return text


def _tidy_spaces(text: str) -> str:
    """抽取常见的毛病：汉字之间被插了空格（“输 入 一 个”）、全角空格、行尾空格。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"(?<=[\u4e00-\u9fff，。；：！？（）、]) (?=[\u4e00-\u9fff，。；：！？（）、])", "", text)
    text = re.sub(r"[ \t]+(?=[，。；：！？、）])|(?<=（)[ \t]+", "", text)   # 中文标点前后的空格
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


# ------------------------------------------------------------ 文字层和示意图
def _merge_boxes(boxes: list[list[float]], gap: float = 6.0) -> list[list[float]]:
    """挨着或重叠的框合成一个（第 5 项是框里的对象数）。"""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        out: list[list[float]] = []
        for b in boxes:
            for o in out:
                if b[0] <= o[2] + gap and o[0] <= b[2] + gap and b[1] <= o[3] + gap and o[1] <= b[3] + gap:
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    o[4] += b[4]
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def _figure_boxes(page) -> list[tuple[float, float, float, float]]:
    """一页里的示意图区域（pdfplumber 坐标：左上角为原点，单位 pt）。

    图片对象直接算；线条、曲线、矩形先按位置聚成块，排除整页的边框、分隔线，
    以及里面全是字的框（样例的底色框、表格——这些字已经在文字里了）。
    """
    W, H = float(page.width), float(page.height)
    figs: list[list[float]] = []
    for im in page.images:
        x0, top, x1, bottom = float(im["x0"]), float(im["top"]), float(im["x1"]), float(im["bottom"])
        if x1 - x0 >= 12 and bottom - top >= 12:
            figs.append([x0, top, x1, bottom, 1])
    vec = []
    for o in list(page.curves) + list(page.lines) + list(page.rects):
        x0, top, x1, bottom = float(o["x0"]), float(o["top"]), float(o["x1"]), float(o["bottom"])
        if x1 - x0 > 0.85 * W and bottom - top > 0.85 * H:
            continue   # 整页的边框
        vec.append([x0, top, x1, bottom, 1])
    for x0, top, x1, bottom, n in _merge_boxes(vec):
        w, h = x1 - x0, bottom - top
        if w < 30 or h < 20:
            continue   # 分隔线、下划线
        chars = sum(1 for c in page.chars
                    if x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and top <= (c["top"] + c["bottom"]) / 2 <= bottom)
        if chars > w * h / 500:
            continue   # 里面密密麻麻全是字：样例框、表格
        figs.append([x0, top, x1, bottom, n])
    merged = _merge_boxes(figs, gap=2.0)
    return [(b[0], b[1], b[2], b[3]) for b in sorted(merged, key=lambda b: (b[1], b[0]))]


def _crop(rendered, box, scale: float = RENDER_SCALE) -> tuple[bytes, int, int]:
    """从渲染好的整页上裁下一块，存成 PNG。"""
    margin = 4
    x0, top, x1, bottom = box
    w, h = rendered.size
    region = (max(0, int((x0 - margin) * scale)), max(0, int((top - margin) * scale)),
              min(w, int((x1 + margin) * scale)), min(h, int((bottom + margin) * scale)))
    img = rendered.crop(region)
    if img.width > 1400:
        img = img.resize((1400, max(1, int(img.height * 1400 / img.width))))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), img.width, img.height


def read_pdf(data: bytes) -> tuple[str, list[dict]] | None:
    """抽 PDF：返回 (原文，带 [图N] 占位, 示意图 [{seq, page, png, width, height}])。打不开返回 None。

    两种抽法：pdfplumber 按行（知道每行的位置，能把 [图N] 插到图所在的地方）、PDFium（浏览器用的那个库，
    有些字体它解得更对）。挑乱码少的；PDFium 那份没有位置，[图N] 放在这一页末尾。
    """
    try:
        import pdfplumber
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        pages_lines: list[str] = []
        pages_flat: list[str] = []
        figures: list[dict] = []
        doc = pdfium.PdfDocument(data)
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for index, page in enumerate(pdf.pages[:MAX_PAGES]):
                    boxes = _figure_boxes(page)[: max(0, MAX_FIGURES - len(figures))]
                    marks = []
                    if boxes:
                        rendered = doc[index].render(scale=RENDER_SCALE).to_pil()
                        for box in boxes:
                            png, w, h = _crop(rendered, box)
                            seq = len(figures) + 1
                            figures.append({"seq": seq, "page": index + 1, "png": png, "width": w, "height": h})
                            marks.append((box, f"[图{seq}]"))
                    items = []
                    for line in page.extract_text_lines():
                        cx, cy = (line["x0"] + line["x1"]) / 2, (line["top"] + line["bottom"]) / 2
                        if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b, _m in marks):
                            continue   # 图里的标注字，图上已经有了
                        items.append((float(line["top"]), line["text"]))
                    items += [(b[1], mark) for b, mark in marks]
                    pages_lines.append("\n".join(t for _top, t in sorted(items, key=lambda x: x[0])))
                    flat = doc[index].get_textpage().get_text_range() or ""
                    pages_flat.append("\n".join([flat] + [mark for _b, mark in marks]))
        finally:
            doc.close()
    except Exception as exc:
        log.warning("PDF 读不了：%s", type(exc).__name__)
        return None
    candidates = [fix_mojibake(_tidy_spaces("\n\n".join(pages_lines))),
                  fix_mojibake(_tidy_spaces("\n\n".join(pages_flat)))]
    best = min(candidates, key=badness)   # 一样好时用第一种（[图N] 在原来的位置）
    return best, figures


def extract(content_type: str, data: bytes) -> tuple[str, str, list[dict]]:
    """题面文件 → (原文, 状态, 示意图)。状态是 ok / layer / garbled / unreadable / failed。大模型整理另做（prepare）。"""
    if not data:
        return "", "failed", []
    if not is_pdf(content_type, data):
        text, status = _plain(data)
        return text, status, []
    got = read_pdf(data)
    if got is None:
        return "", "unreadable", []
    text, figures = got
    return text, ("layer" if garbled(FIG_MARK.sub("", text)) is None else "garbled"), figures


def _plain(data: bytes) -> tuple[str, str]:
    from .xdoj.util import decode_bytes, detect_encoding

    text, ok = decode_bytes(data, detect_encoding(data))
    if not ok or not (text or "").strip():
        return "", "failed"
    if "<html" in text.lower() or "<body" in text.lower() or "<p" in text.lower():
        text = re.sub(r"<(script|style)[\s\S]*?</\1>", "", text, flags=re.I)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
    text = _tidy_spaces(text)
    return (text, "ok") if garbled(text) is None else ("", "failed")


# ------------------------------------------------------------ 大模型整理
_SYSTEM = """你在整理一道 C 语言程序设计题的题面。下面的文字是程序从 PDF 里抽出来的，可能有这些毛病：
多余的空格和断行、行的顺序乱了、上标下标被拆开（比如 10^5 抽成了“10 5”）、≤ ≥ ≠ × 这类符号丢了或者变成怪字、个别字是乱码。

请整理成通顺的纯文本：
- 按原文分成这几部分（原文没有的部分就不写）：题目描述、输入格式、输出格式、样例输入、样例输出、提示；每部分标题单独一行；
- 样例的输入、输出逐行照抄，一个字符都不能改，保留原来的换行；
- 不许增加原文没有的内容；不许改数字；不许解释题目、不许给思路和代码；
- [图1] 这样的标记是示意图的位置（你看不到图），原样留在原来的位置；
- 实在认不出的字，按上下文最可能的字还原，并把 restored 设为 true。

只输出 JSON：{"text": "整理好的题面", "restored": true 或 false}"""

_HEADINGS = ("题目描述", "输入格式", "输出格式", "样例输入", "样例输出", "提示", "输入", "输出", "样例")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _cjk(text: str) -> list[str]:
    return [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]


def check_tidy(raw: str, out: str, raw_garbled: bool) -> str | None:
    """大模型整理的结果和原文对不对得上。对不上返回原因（程序核对，不靠大模型自己说）。"""
    if not out.strip():
        return "是空的"
    if sorted(FIG_MARK.findall(raw)) != sorted(FIG_MARK.findall(out)):
        return "[图N] 的标记丢了或多了"
    raw_body, out_body = FIG_MARK.sub("", raw), FIG_MARK.sub("", out)
    if garbled(out_body) is not None:
        return "整理后还是乱码"
    src_nums, out_nums = set(_NUM.findall(raw_body)), set(_NUM.findall(out_body))
    if out_nums - src_nums:
        return "出现了原文没有的数字：" + "、".join(sorted(out_nums - src_nums)[:5])
    if len(src_nums) >= 3 and len(src_nums & out_nums) < 0.9 * len(src_nums):
        return "原文的数字丢了（样例不能丢）"
    n_raw, n_out = len("".join(raw_body.split())), len("".join(out_body.split()))
    if not 0.5 * n_raw <= n_out <= 1.6 * n_raw + 40:
        return "长度和原文差太多"
    if not raw_garbled:
        body = out_body
        for heading in _HEADINGS:
            body = body.replace(heading, "")
        chars, known = _cjk(body), set(_cjk(raw_body))
        if chars and sum(1 for ch in chars if ch in known) < 0.9 * len(chars):
            return "加了原文没有的内容"
    return None


def tidy(raw: str, status: str) -> tuple[str, str] | None:
    """交给大模型整理。返回 (文字, 状态 llm / llm_guess)；没有大模型或核对不过返回 None（沿用程序抽的文字）。"""
    from . import llm

    if not llm.available() or not raw.strip():
        return None
    raw_garbled = status == "garbled"

    def validate(d: dict) -> str:
        text = str(d.get("text") or "").strip()
        problem = check_tidy(raw, text, raw_garbled)
        if problem:
            raise ValueError(problem)
        return text

    try:
        text = llm.call_json("statement_tidy", _SYSTEM, "【抽出来的题面】\n" + raw[:6000], validate,
                             retries=1, max_tokens=2500)
    except llm.LLMUnavailable as exc:
        log.info("题面整理没成功（%s），用程序抽的文字", exc)
        return None
    return text, ("llm_guess" if raw_garbled else "llm")


# ------------------------------------------------------------ 存库、读库
def store(s, row, content_type: str, data: bytes) -> bool:
    """题面文件抽出原文和示意图，写进 ProblemText 这一行和示意图表。给人看的文字变了返回 True。
    大模型整理另做（prepare：标注这道题之前、后台空闲时）。"""
    from sqlalchemy import delete

    from . import models as m

    text, status, figures = extract(content_type, data)
    old = row.extracted_text or ""
    row.raw_text, row.extract_status, row.tidied_at = text, status, None
    row.extracted_text = text if status in USABLE else ""
    s.execute(delete(m.ProblemFigure).where(m.ProblemFigure.server_id == row.server_id,
                                            m.ProblemFigure.problem_id == row.problem_id))
    for f in figures:
        s.add(m.ProblemFigure(server_id=row.server_id, problem_id=row.problem_id, seq=f["seq"], page=f["page"],
                              png=f["png"], width=f["width"], height=f["height"]))
    log.info("题目 %s 的题面：%s，%d 字，%d 张示意图", row.problem_id, STATUS_NAMES.get(status, status),
             len(text), len(figures))
    return row.extracted_text != old


def prepare(s, server_id: int, problem_id: int) -> bool:
    """抽出来、还没交给大模型整理过的 PDF 题面（layer / garbled）整理一次。给人看的文字变了返回 True。

    标注这道题之前调用（标注用的就是整理好的题面）；后台空闲时也会把没整理的补上。每个文件只整理一次。
    """
    from . import db, llm
    from . import models as m

    row = s.get(m.ProblemText, (server_id, problem_id))
    if row is None or row.extract_status not in ("layer", "garbled") or row.tidied_at is not None or not llm.available():
        return False
    got = tidy(row.raw_text or row.extracted_text or "", row.extract_status)
    row.tidied_at = db.now()
    if got is None:
        return False
    text, status = got
    changed = text != (row.extracted_text or "")
    row.extracted_text, row.extract_status = text, status
    log.info("题目 %s 的题面：%s", problem_id, STATUS_NAMES.get(status, status))
    return changed


def needs_redo(status: str | None, file_bytes: bytes | None, content_type: str = "") -> bool:
    """库里已有的 PDF 题面要不要重新抽：以前的程序（直接抽文字、OCR 那一版）处理的。"""
    return is_pdf(content_type, file_bytes) and status not in _PDF_STATUS


def usable_text(row) -> str:
    """题面文字（ProblemText 行）；乱码、没整理出来的返回空串。"""
    if row is None or (row.extract_status or "") not in USABLE:
        return ""
    return row.extracted_text or ""


def page_count(row) -> int:
    """题面 PDF 有几页（最多 MAX_PAGES）；不是 PDF 返回 0。"""
    if row is None or not is_pdf(row.content_type, (row.file_bytes or b"")[:5]):
        return 0
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(row.file_bytes)
        try:
            return min(len(doc), MAX_PAGES)
        finally:
            doc.close()
    except Exception:
        return 0


_page_cache: dict[tuple, bytes] = {}


def page_png(row, n: int) -> bytes | None:
    """题面 PDF 的第 n 页渲染成 PNG，裁掉四周的空白（右栏窄，留白太多字就小了）。"""
    if not 1 <= n <= page_count(row):
        return None
    key = (row.server_id, row.problem_id, row.file_hash, n)
    if key in _page_cache:
        return _page_cache[key]
    import pypdfium2 as pdfium
    from PIL import ImageChops, Image

    doc = pdfium.PdfDocument(row.file_bytes)
    try:
        img = doc[n - 1].render(scale=RENDER_SCALE).to_pil().convert("RGB")
    finally:
        doc.close()
    box = ImageChops.difference(img, Image.new("RGB", img.size, "white")).getbbox()
    if box:
        pad = 16
        img = img.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad)))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    if len(_page_cache) > 64:
        _page_cache.clear()
    _page_cache[key] = buf.getvalue()
    return _page_cache[key]


def for_llm(text: str) -> str:
    """发给大模型的题面：有示意图时说明 [图N] 是什么（大模型看不到图）。"""
    if text and FIG_MARK.search(text):
        return text + "\n（题面里的 [图N] 是示意图的位置，你看不到图；不要编造图里的内容）"
    return text


def redo_stored(s, limit: int = 2) -> int:
    """后台空闲时自动做：以前的程序处理的 PDF 题面重新抽一遍（文件本身存着，不用再请求 XDOJ）；
    抽好了还没交给大模型整理的整理一遍。有变化就重新标注这道题。返回处理了几道。"""
    from sqlalchemy import func, select

    from . import llm
    from . import models as m
    from .xdoj.store import enqueue

    T = m.ProblemText
    done = 0
    cands = s.execute(select(T.server_id, T.problem_id, T.extract_status, T.content_type, func.substr(T.file_bytes, 1, 5))
                      .where(T.extract_status.not_in(sorted(_PDF_STATUS)))
                      .order_by(T.server_id, T.problem_id)).all()
    for server_id, problem_id, status, ctype, head in cands:
        if done >= limit:
            return done
        if not needs_redo(status, bytes(head or b""), ctype or ""):   # 只读了前 5 个字节，够判断是不是 PDF
            continue
        row = s.get(T, (server_id, problem_id))
        store(s, row, row.content_type, row.file_bytes)
        enqueue(s, server_id, "label_problem", problem_id, 2)   # 标注前会先交给大模型整理
        done += 1
    if llm.available() and done < limit:
        pending = s.execute(select(T.server_id, T.problem_id).where(
            T.extract_status.in_(("layer", "garbled")), T.tidied_at.is_(None))
            .order_by(T.server_id, T.problem_id).limit(limit - done)).all()
        for server_id, problem_id in pending:
            if prepare(s, server_id, problem_id):
                enqueue(s, server_id, "label_problem", problem_id, 2)
            done += 1
    return done
