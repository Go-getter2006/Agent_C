"""离线将 Word 的章节和原句对齐到 PDF 坐标，发布小节的版面切片。"""
from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pypdfium2 as pdfium
from pypdfium2 import raw

from .textbook import BUNDLE_VERSION, _figure_number

Box = tuple[float, float, float, float]
_SKIP = re.compile(r"[\s\u00ad\u200b\ufeff]")
_PREFIX = re.compile(r"^(?:第\s*\d+\s*章|\d+(?:[.．]\d+)+|\d+[.．、])\s*")
_SENTENCE = re.compile(r"[^。！？\n]+[。！？]?")


def _key(value: str) -> str:
    return _SKIP.sub("", unicodedata.normalize("NFKC", value)).casefold()


@dataclass
class Glyph:
    page: int
    rect: Box


def _union(a: Box, b: Box) -> Box:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


class _Layout:
    def __init__(self, doc: pdfium.PdfDocument):
        self.doc = doc
        self.glyphs: list[Glyph] = []
        self.images: list[list[Box]] = []
        self.sizes: list[tuple[float, float]] = []
        chunks = []
        for index in range(len(doc)):
            with closing(doc[index]) as page:
                self.sizes.append(page.get_size())
                self.images.append([obj.get_bounds() for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE])])
                with closing(page.get_textpage()) as text_page:
                    for i in range(text_page.count_chars()):
                        codepoint = raw.FPDFText_GetUnicode(text_page, i)
                        if not codepoint or codepoint > 0x10ffff:
                            continue
                        chars = _key(chr(codepoint))
                        if not chars:
                            continue
                        left, bottom, right, top = text_page.get_charbox(i)
                        if top <= bottom or right <= left:
                            continue
                        chunks.append(chars)
                        self.glyphs.extend([Glyph(index, (left, bottom, right, top))] * len(chars))
        self.text = "".join(chunks)

    def headings(self, sections: list[dict]) -> list[int]:
        starts, cursor = [], 0
        for section in sections:
            title = section["title"]
            for key in dict.fromkeys((_key(title), _key(_PREFIX.sub("", title)))):
                found = self.text.find(key, cursor) if key else -1
                if found >= 0:
                    starts.append(found)
                    cursor = found + len(key)
                    break
            else:
                raise ValueError(f"无法在对应 PDF 定位小节 {title}，请核对 Word 与 PDF 版本")
        return starts

    def line_boxes(self, start: int, end: int) -> list[tuple[int, Box]]:
        lines: list[tuple[int, Box]] = []
        for g in self.glyphs[start:end]:
            if lines and lines[-1][0] == g.page:
                _, last = lines[-1]
                if min(last[3], g.rect[3]) - max(last[1], g.rect[1]) > 2 and g.rect[0] >= last[0] - 2:
                    lines[-1] = (g.page, _union(last, g.rect))
                    continue
            lines.append((g.page, g.rect))
        return lines


def _snippets(section: dict):
    for block in section["blocks"]:
        if block["kind"] == "code":
            for line in block["text"].splitlines():
                if line.strip():
                    yield line.strip()
            continue
        if block["kind"] == "table":
            parts = [part for row in block["rows"] for cell in row for part in cell]
        else:
            parts = block["parts"]
        for part in parts:
            if isinstance(part, str):
                for sentence in _SENTENCE.findall(part):
                    if sentence.strip():
                        yield sentence.strip()


def _section(layout: _Layout, section: dict, start: int, end: int, archive: ZipFile, stored: set) -> dict:
    first = layout.glyphs[start]
    after = layout.glyphs[end] if end < len(layout.glyphs) else None
    last_page = after.page if after else len(layout.sizes) - 1
    viewports = {}
    pages = []
    for number in range(first.page, last_page + 1):
        width, height = layout.sizes[number]
        upper = min(height, first.rect[3] + 5) if number == first.page else height
        lower = min(upper, after.rect[3] + 5) if after and number == after.page else 0
        # 下一小节从新页首行开始，前一节不带这页的空页眉。
        if upper - lower < 76 and number != first.page:
            continue
        if upper <= lower:
            raise ValueError(f"小节版面范围有误：{section['title']}")
        left = width * 0.035
        right = width - left
        with closing(layout.doc[number]) as page:
            with closing(page.render(scale=1.8, crop=(left, lower, width - right, height - upper))) as bitmap:
                with io.BytesIO() as buffer:
                    bitmap.to_pil().save(buffer, "PNG")
                    png = buffer.getvalue()
                size = (bitmap.width, bitmap.height)
        digest = hashlib.sha256(png).hexdigest()
        if digest not in stored:
            archive.writestr(f"pages/{digest}.png", png, compress_type=ZIP_STORED)
            stored.add(digest)
        viewports[number] = (len(pages), left, lower, right, upper)
        pages.append({"image": digest, "width": size[0], "height": size[1]})

    def map_boxes(lines: list[tuple[int, Box]]) -> list[list]:
        marks = []
        for page, (x0, y0, x1, y1) in lines:
            if page not in viewports:
                continue
            i, left, lower, right, upper = viewports[page]
            x0, x1 = max(x0, left), min(x1, right)
            y0, y1 = max(y0, lower), min(y1, upper)
            if x1 > x0 and y1 > y0:
                marks.append([i, *(round(value, 6) for value in (
                    (x0 - left) / (right - left), (upper - y1) / (upper - lower),
                    (x1 - x0) / (right - left), (y1 - y0) / (upper - lower)))])
        return marks

    anchors = []
    cursor = start
    for text in _snippets(section):
        key = _key(text)
        figure_number = _figure_number(text)
        if (len(key) < 6 and not figure_number) or len(text) > 500:
            continue
        found = layout.text.find(key, cursor, end)
        if found < 0:
            continue
        cursor = found + len(key)
        lines = layout.line_boxes(found, cursor)
        if boxes := map_boxes(lines):
            anchors.append({"id": len(anchors) + 1, "text": text, "boxes": boxes})
            if size := section.get("figure_sizes", {}).get(text):
                anchors[-1]["wordFigure"] = size
        if figure_number and lines:
            page, caption = lines[0]
            if page not in viewports:
                continue
            base = max(caption[3], viewports[page][2])
            ceiling = viewports[page][4]
            candidates = [box for box in layout.images[page]
                          if base - 3 <= box[1] <= base + 36 and box[3] <= ceiling
                          and box[2] - box[0] >= 28 and box[3] - box[1] >= 18]
            if candidates:
                figure = min(candidates, key=lambda box: abs(box[1] - base))
                if boxes := map_boxes([(page, figure)]):
                    anchors.append({"id": len(anchors) + 1, "text": text, "boxes": boxes, "figure": True})
    return {key: section[key] for key in ("id", "parent", "title")} | {"pages": pages, "anchors": anchors}


def render(output: Path, title: str, sections: list[dict], pdf_directory: Path) -> dict:
    """先建临时包。成功后原子改名，避免将不完整的书发布到服务端。"""
    from tempfile import TemporaryDirectory

    if output.exists():
        raise FileExistsError(output)
    rendered, stored = [], set()
    with TemporaryDirectory(dir=output.parent, prefix=".textbook-") as work:
        temporary = Path(work) / "bundle.zip"
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for source, group in groupby(sections, key=lambda section: section["source"]):
                with pdfium.PdfDocument(pdf_directory / f"{source}.pdf") as document:
                    batch = list(group)
                    layout = _Layout(document)
                    starts = layout.headings(batch)
                    ends = starts[1:] + [len(layout.glyphs)]
                    rendered.extend(_section(layout, section, begin, end, archive, stored)
                                    for section, begin, end in zip(batch, starts, ends))
            archive.writestr("textbook.json", json.dumps({"version": BUNDLE_VERSION, "title": title,
                                                          "sections": rendered}, ensure_ascii=False))
        temporary.rename(output)
    return {"page_images": len(stored), "citation_anchors": sum(len(row["anchors"]) for row in rendered),
            "figure_anchors": sum(a.get("figure", False) for row in rendered for a in row["anchors"])}
