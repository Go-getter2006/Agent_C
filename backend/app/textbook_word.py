"""离线把 Word 教材整理成可在私有服务端导入的分节数据。"""
from __future__ import annotations

import posixpath
import re
import unicodedata
from contextlib import ExitStack
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .textbook_layout import render

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
CHAPTER = re.compile(r"^第\s*(\d+)\s*章\s*(.+)$")
SECTION = re.compile(r"^(\d+(?:[.．]\d+)+)(?![.．\d])\s*(.+)$")
HEADING = re.compile(r"(?:heading|标题)\s*(\d)$", re.IGNORECASE)
EXERCISES = re.compile(r"(?:本章|章末|课后)?(?:习题|练习题?|思考题|思考与练习|复习与思考)"
                       r"(?:[0-9]+|[一二三四五六七八九十]+)?")
CODE = re.compile(r"^(?:#\s*(?:include|define)\b|//|/\*|\*/|[{}]|(?:int|void|char|float|double|"
                  r"long|short|unsigned|struct|return|if|else|while|for|do|switch|case)\b.*[{};:]|"
                  r"\w+\s*\(.*\)\s*;)", re.ASCII)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _heading(text: str) -> tuple[tuple[int, ...], str] | None:
    if match := CHAPTER.fullmatch(text):
        return (int(match[1]),), match[2].strip()
    if match := SECTION.fullmatch(text):
        return tuple(map(int, match[1].replace("．", ".").split("."))), match[2].strip()
    return None


def _plain(parts: list) -> str:
    return "".join(part for part in parts if isinstance(part, str))


def _number_title(number: tuple[int, ...], title: str) -> str:
    prefix = f"第{number[0]}章" if len(number) == 1 else ".".join(map(str, number))
    title = re.sub(r"\s+", " ", title).strip()
    return f"{prefix} {title}"


class _Document:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        self.body = ET.fromstring(archive.read("word/document.xml")).find(W + "body")
        if self.body is None:
            raise ValueError("Word 文件没有正文")
        self.styles = {}
        if "word/styles.xml" in archive.namelist():
            for style in ET.fromstring(archive.read("word/styles.xml")).findall(W + "style"):
                self.styles[style.get(W + "styleId")] = style
        self.rels = {}
        if "word/_rels/document.xml.rels" in archive.namelist():
            for rel in ET.fromstring(archive.read("word/_rels/document.xml.rels")):
                if rel.get("TargetMode") == "External" or not rel.get("Type", "").endswith("/image"):
                    continue
                target = rel.get("Target", "")
                name = posixpath.normpath(posixpath.join("word", target))
                if name.startswith("word/media/"):
                    self.rels[rel.get("Id")] = name

    def level(self, p: ET.Element) -> int:
        outline = p.find(f"{W}pPr/{W}outlineLvl")
        if outline is not None:
            value = int(outline.get(W + "val", "9"))
            return value + 1 if value < 9 else 0
        style = p.find(f"{W}pPr/{W}pStyle")
        seen = set()
        key = style.get(W + "val") if style is not None else None
        while key in self.styles and key not in seen:
            seen.add(key)
            definition = self.styles[key]
            name = definition.find(W + "name")
            if match := HEADING.search(name.get(W + "val", "") if name is not None else ""):
                return int(match[1])
            level = definition.find(f"{W}pPr/{W}outlineLvl")
            if level is not None and int(level.get(W + "val", "9")) < 9:
                return int(level.get(W + "val")) + 1
            base = definition.find(W + "basedOn")
            key = base.get(W + "val") if base is not None else None
        return 0

    def parts(self, node: ET.Element, image=None) -> list:
        """保持文字、换行、内嵌图的次序。跳过域指令、删除的修订和 OLE 原件。"""
        tag = node.tag.rsplit("}", 1)[-1]
        if node.tag in (W + "t", M + "t"):
            return [node.text or ""]
        if node.tag == W + "tab":
            return ["\t"]
        if node.tag in (W + "br", W + "cr"):
            return ["\n"]
        if tag in ("del", "instrText", "OLEObject", "lastRenderedPageBreak"):
            return []
        if tag == "AlternateContent":
            for alternative in node:
                if parts := self.parts(alternative, image):
                    return parts
            return []
        if tag in ("blip", "imagedata"):
            target = self.rels.get(node.get(R + "embed") or node.get(R + "id"))
            return [{"figure": True}] if image and target else []
        if node.tag in (M + "f", M + "sSub", M + "sSup"):
            names = ("num", "den") if tag == "f" else ("e", "sub" if tag == "sSub" else "sup")
            a, b = (_plain(self.parts(child)) if (child := node.find(M + name)) is not None else ""
                    for name in names)
            return [f"({a})/({b})" if tag == "f" else f"{a}{'_' if tag == 'sSub' else '^'}({b})"]
        collected = []
        for child in node:
            for part in self.parts(child, image):
                if isinstance(part, str) and collected and isinstance(collected[-1], str):
                    collected[-1] += part
                else:
                    collected.append(part)
        return collected


def _contents(doc: _Document | None) -> dict[tuple[int, ...], str]:
    headings = {}
    if doc:
        for paragraph in doc.body.findall(W + "p"):
            label, tab, page = _plain(doc.parts(paragraph)).strip().rpartition("\t")
            if tab and page.strip().isdigit() and (found := _heading(label.strip())):
                headings[found[0]] = found[1]
    return headings


def prepare(output: Path, title: str, files: list[Path], pdf_directory: Path, contents: Path | None = None) -> dict:
    """用 Word 正文建立层级，用同版 PDF 定位引用并生成版面图。"""
    sections = []
    stack = []
    seen_numbers = set()
    number: tuple[int, ...] = ()

    def start(label: str, level: int, source: str) -> None:
        while stack and stack[-1][0] >= level:
            stack.pop()
        section = {"id": len(sections) + 1, "parent": stack[-1][1]["id"] if stack else None,
                   "title": label, "source": source, "blocks": []}
        sections.append(section)
        stack.append((level, section))

    def add(block: dict) -> None:
        blocks = stack[-1][1]["blocks"]
        if block["kind"] == "code" and blocks and blocks[-1]["kind"] == "code":
            blocks[-1]["text"] += "\n" + block["text"]
        else:
            blocks.append({"id": len(blocks) + 1, **block})

    with ExitStack() as opened:
        toc = _contents(_Document(opened.enter_context(ZipFile(contents))) if contents else None)
        for path in files:
            archive = opened.enter_context(ZipFile(path))
            doc = _Document(archive)
            in_exercises = False
            for element in doc.body:
                if element.tag == W + "tbl" and stack:
                    rows = [[[part for p in cell.findall(W + "p") for part in [*doc.parts(p, True), "\n"]][:-1]
                             for cell in row.findall(W + "tc")] for row in element.findall(W + "tr")]
                    if any(rows):
                        add({"kind": "table", "rows": rows})
                    continue
                if element.tag != W + "p":
                    continue
                label = _plain(doc.parts(element)).strip()
                if stack and not in_exercises and EXERCISES.fullmatch(_norm(label)):
                    start(_norm(label), 2, path.stem)
                    in_exercises = True
                    continue
                level = doc.level(element)
                found = _heading(label) if len(label) < 96 else None
                # 章末的 1.4、3.12 是题号。直到下一章，题目和选项都留在习题正文中。
                if in_exercises and (found is None or len(found[0]) != 1):
                    level, found = 0, None
                if found and len(found[0]) > 1 and not level:
                    section_no, section_title = found
                    if (number and section_no[0] != number[0]) or section_no <= number \
                            or section_no[:-1] not in seen_numbers \
                            or not re.match(r"[\u3400-\u9fffA-Za-z]", section_title) \
                            or re.search(r"[。！？；]", section_title):
                        found = None
                if not in_exercises:
                    if found is None and label and (level or element.find(f"{W}pPr/{W}numPr") is not None):
                        found = next(((key, name) for key, name in toc.items() if key not in seen_numbers
                                      and key > number and _norm(label) == _norm(name)), None)
                if found:
                    number, name = found
                    if len(number) == 1:
                        in_exercises = False
                    seen_numbers.add(number)
                    start(_number_title(number, name), len(number), path.stem)
                    continue
                if level and label and stack:
                    start(label, max(2, level), path.stem)
                    continue
                if not stack:
                    continue
                parts = doc.parts(element, True)
                if not label and not any(isinstance(part, dict) for part in parts):
                    continue
                text = _plain(parts)
                fonts = {font.get(W + "ascii", "").lower() for font in element.iter(W + "rFonts")}
                monospace = any("courier" in font or "consolas" in font for font in fonts)
                if all(isinstance(part, str) for part in parts) and (monospace or CODE.match(text.lstrip())):
                    add({"kind": "code", "text": text.rstrip()})
                else:
                    add({"kind": "text", "parts": parts})
    if not sections:
        raise ValueError("没有识别到教材章节")
    counts = render(output, title, sections, pdf_directory)
    return {**counts, "sections": len(sections), "numbered": len(seen_numbers),
            "contents_only": [".".join(map(str, key)) for key in toc if key not in seen_numbers]}
