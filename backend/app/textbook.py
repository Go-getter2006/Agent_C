"""C 语言教材的私有发布包、检索和引文标记。"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from zipfile import ZipFile

from PIL import Image
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from . import models as m
from .chat.text import definition_subject, previous_question_for_followup

BUNDLE_VERSION = 2
_DIGEST = re.compile(r"[0-9a-f]{64}")
_WORDS = re.compile(r"[\u3400-\u9fff]+|%[a-z]+|[a-z_]\w*|[0-9]+(?:[.][0-9]+)+|&&|\|\||[&*]", re.ASCII)
_STOP = set("什么 怎么 如何 一下 是不是 为什么 这个 那个 有什么 给我 帮我 有哪些 是什么 和例 解释 区别 想问 能否 能不".split())
_QUESTION_WORDS = re.compile(r"为什么|是什么|这是什么|怎么|如何|帮我|解释一下|请问")
_FIG_NUMBER = re.compile(r"图(?:\d+(?:[.．-]\d+)*|[一二三四五六七八九十百]+)")


def _figure_number(text):
    match = _FIG_NUMBER.match(re.sub(r"\s+", "", text))
    return match[0] if match else ""


def publish(s: Session, path: Path) -> tuple[m.TextbookEdition, bool]:
    """原件留在离线资料目录，服务端只导入经过核验的版面和引用索引。"""
    with ZipFile(path) as archive:
        manifest = archive.read("textbook.json")
        digest = hashlib.sha256(manifest).hexdigest()
        book = json.loads(manifest)
        if book.get("version") != BUNDLE_VERSION or not book.get("sections"):
            raise ValueError("请使用生成好的教材阅读包")
        existing = s.scalar(select(m.TextbookEdition).where(m.TextbookEdition.digest == digest))
        if existing is not None:
            return existing, False
        edition = m.TextbookEdition(title=book["title"], digest=digest)
        s.add(edition)
        s.flush()
        section_ids, figure_ids = set(), set()
        for section in book["sections"]:
            pages = section["pages"]
            section_id, parent = section["id"], section["parent"]
            if section_id in section_ids or (parent is not None and parent not in section_ids):
                raise ValueError("教材目录层级有误")
            section_ids.add(section_id)
            for page in pages:
                key = page["image"]
                if not _DIGEST.fullmatch(key):
                    raise ValueError("教材版面文件编号有误")
                if key not in figure_ids:
                    png = archive.read(f"pages/{key}.png")
                    if not png.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(png).hexdigest() != key:
                        raise ValueError("教材版面图片校验失败")
                    s.add(m.TextbookFigure(edition_id=edition.id, digest=key, png=png))
                    figure_ids.add(key)
            s.add(m.TextbookSection(edition_id=edition.id, number=section_id, parent=parent,
                                    title=section["title"], pages=pages, anchors=section["anchors"]))
        if not figure_ids:
            raise ValueError("教材阅读包没有可展示的版面")
    return edition, True


def active(s: Session) -> m.TextbookEdition | None:
    return s.scalar(select(m.TextbookEdition).order_by(m.TextbookEdition.id.desc()).limit(1))


@dataclass(frozen=True)
class Directory:
    edition: m.TextbookEdition
    entries: dict[int, tuple[int | None, str]]

    def prompt(self) -> str:
        depths = {}
        lines = []
        for number, (parent, title) in self.entries.items():
            depth = depths[number] = depths.get(parent, -1) + 1
            lines.append(f"{'  ' * depth}[{number}] {title}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Lookup:
    sections: tuple[int, ...]
    keywords: str = ""


def directory(s: Session, course: str) -> Directory | None:
    if course != "C" or (edition := active(s)) is None:
        return None
    sections = m.TextbookSection
    entries = {number: (parent, title) for number, parent, title in s.execute(
        select(sections.number, sections.parent, sections.title).where(sections.edition_id == edition.id)
        .order_by(sections.number))}
    return Directory(edition, entries) if entries else None


def _terms(text: str) -> frozenset[str]:
    tokens = set()
    for word in _WORDS.findall(unicodedata.normalize("NFKC", text).lower()):
        if "\u3400" <= word[0] <= "\u9fff":
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.add(word)
    return frozenset(tokens - _STOP)


@dataclass(frozen=True)
class Passage:
    reference: dict
    text: str
    words: frozenset[str]
    title_words: frozenset[str]
    figure: bool


_cache: OrderedDict[str, tuple[list[Passage], Counter]] = OrderedDict()
_cache_lock = Lock()


def _passages(s: Session, edition: m.TextbookEdition) -> tuple[list[Passage], Counter]:
    with _cache_lock:
        if edition.digest in _cache:
            _cache.move_to_end(edition.digest)
            return _cache[edition.digest]
        passages, frequency, paths = [], Counter(), {}
        for section in s.scalars(select(m.TextbookSection).where(m.TextbookSection.edition_id == edition.id)
                                 .order_by(m.TextbookSection.number)):
            path = (*paths.get(section.parent, ()), section.title)
            paths[section.number] = path
            title_words = _terms(" ".join(path[-2:]))
            anchors = section.anchors
            for i, anchor in enumerate(anchors):
                words = _terms(anchor["text"])
                if not words or not anchor["boxes"]:
                    continue
                ref = {"section": section.number, "anchor": anchor["id"], "path": list(path)}
                text = anchor["text"]
                number = _figure_number(text)
                previous = anchors[i - 1]["text"] if i else ""
                figure = anchor.get("figure", False)
                if figure and i >= 2 and previous == text:
                    previous = anchors[i - 2]["text"]
                mentions = _FIG_NUMBER.findall(re.sub(r"\s+", "", previous))
                if number and not figure:
                    if i + 1 < len(anchors) and anchors[i + 1].get("figure") and anchors[i + 1]["text"] == text:
                        continue
                    figure = bool(anchor.get("wordFigure") or number in mentions)
                if figure and number in mentions:
                    text += "\n引图原句：" + previous
                passages.append(Passage(ref, text, words, title_words, figure))
                frequency.update(words)
        _cache[edition.digest] = passages, frequency
        if len(_cache) > 2:
            _cache.popitem(last=False)
        return passages, frequency


def retrieve(s: Session, course: str, question: str, history: list[tuple[str, str]],
             book: Directory | None = None, lookup: Lookup | None = None) -> dict:
    """只检索本轮问到的教材片段。沿用本轮答复调用，无需单独请求模型。"""
    term = definition_subject(question)
    query = _terms(term or _QUESTION_WORDS.sub(" ", question))
    if lookup:
        query |= _terms(lookup.keywords)
    previous = previous_question_for_followup(question, history)
    remembered = _terms(definition_subject(previous) or previous)
    if not query:
        query = remembered
    remembered -= query
    if course != "C" or (not query and not lookup) or (edition := book.edition if book else active(s)) is None:
        return {}
    groups = {}
    if lookup and book:
        selected = set(lookup.sections)
        for number, (parent, _) in book.entries.items():
            if number in selected:
                groups[number] = number
            elif parent in groups:
                groups[number] = groups[parent]
    passages, frequency = _passages(s, edition)
    weights = {term: math.log(1 + len(passages) / (1 + frequency[term])) for term in query | remembered}
    ranked = []
    for passage in passages:
        if lookup and passage.reference["section"] not in groups:
            continue
        in_text = query & passage.words
        carried = remembered & passage.words
        if not lookup and not in_text and len(carried) < 2:
            continue
        if not lookup and len(query) >= 3 and len((passage.words | passage.title_words) & query) < 2 and len(carried) < 2:
            continue
        score = sum(weights[t] for t in in_text)
        score += sum(weights[t] * (1.6 if t.isascii() and t[0].isalpha() else 0.65)
                     for t in query & passage.title_words)
        score += 0.28 * sum(weights[t] for t in carried)
        if term and passage.text.startswith((term + "是", term + "指", term + "由", term + "表示")):
            score += sum(weights[t] for t in in_text)
        if passage.figure and re.search(r"\d+(?:\.\d+)+", question):
            score *= 2
        ranked.append((score, passage))
    ranked.sort(key=lambda item: (item[0], item[1].figure), reverse=True)
    if groups:
        # 每处先取最相关的一段，再补其余候选，避免较长的章节吃掉所有位置。
        seen = set()
        first, rest = [], []
        for entry in ranked:
            group = groups[entry[1].reference["section"]]
            (rest if group in seen else first).append(entry)
            seen.add(group)
        ranked = first + rest
    texts = [item for _, item in ranked if not item.figure][:5]
    if groups:
        # 旧版次只有图题、缺少图形边界时也给模型看，仍可引用图题并打开整页。
        texts.extend(item for _, item in ranked if not item.figure and _figure_number(item.text) and item not in texts)
    figures = [item for _, item in ranked if item.figure]
    if not groups:
        figures = figures[:2]
    return {f"{prefix}{i}": (item.text, dict(item.reference, edition=edition.id), item.figure)
            for prefix, candidates in (("T", texts), ("F", figures)) for i, item in enumerate(candidates, 1)}


def prompt(matches: dict) -> str:
    if not matches:
        return ""
    lines = [f"[{key}] {' › '.join(ref['path'])}（{'图题' if figure else '仅有题注' if _figure_number(text) else '原文'}）\n{text}"
             for key, (text, ref, figure) in matches.items()]
    return "【本轮检索到的教材资料】\n" + "\n\n".join(lines)


def citations(matches: dict, selected) -> list[dict]:
    if not isinstance(selected, list) or len(selected) > 2:
        raise ValueError("textbook 请选最有帮助的一到两处，无关时填 []")
    refs = {}
    for choice in selected:
        if not isinstance(choice, dict):
            raise ValueError("每处教材引用要有 id、role 和 explanation")
        key = str(choice.get("id", "")).strip(" []").upper()
        note = choice.get("explanation")
        role = choice.get("role")
        if key not in matches or role not in ("key", "example") or not isinstance(note, str) or not note.strip():
            raise ValueError("请用本轮候选编号，标明 key 或 example，并用一句话解释这处原文")
        refs[key] = {**matches[key][1], "role": role, "explanation": note.strip()}
        fig = choice.get("figureId") or (key if matches[key][2] else None)
        if fig:
            fig = str(fig).strip(" []").upper()
            if fig not in matches or not matches[fig][2] or matches[fig][1]["section"] != refs[key]["section"]:
                raise ValueError("figureId 只能选同一小节中的候选图号")
            refs[key]["figureAnchor"] = matches[fig][1]["anchor"]
    return list(refs.values())


def _line_marks(boxes, pages):
    """把同一原句、同一行的相邻字形合成行框。旧书里的“一”、句点等可能只有笔画高度。"""
    lines = []
    for page, x, y, w, h in boxes:
        if lines and lines[-1][0] == page:
            _, left, top, width, height = lines[-1]
            em = max(h, height)
            gap = (x - left - width) * pages[page]["width"]
            overlap = min(y + h, top + height) - max(y, top)
            if x >= left and gap <= em * pages[page]["height"] and overlap >= .3 * min(h, height):
                right, bottom = max(left + width, x + w), max(top + height, y + h)
                lines[-1] = [page, left, min(top, y), right - left, bottom - min(top, y)]
                continue
        lines.append([page, x, y, w, h])
    return lines


@lru_cache(maxsize=16)
def _ink_bounds(png: bytes, top: int, bottom: int):
    """在引图句与图题之间读取印刷内容的包围盒，兼容 Word 绘制的箭头和矢量框。"""
    with Image.open(io.BytesIO(png)) as image:
        band = image.crop((0, top, image.width, bottom)).convert("L")
        return band.point(lambda gray: 255 if gray < 160 else 0).getbbox()


def _figure_frame(s, section, selected):
    anchors = section.anchors
    for i, caption in enumerate(anchors):
        if caption["id"] != selected or not _figure_number(caption["text"]) or not caption["boxes"] or not i:
            continue
        previous = anchors[i - 1]
        page = caption["boxes"][0][0]
        if caption.get("figure"):
            parts = list(caption["boxes"])
            if previous["text"] == caption["text"]:
                parts.extend(b for b in previous["boxes"] if b[0] == page)
            left, y = min(b[1] for b in parts), min(b[2] for b in parts)
            return [page, left, y, max(b[1] + b[3] for b in parts) - left, max(b[2] + b[4] for b in parts) - y]
        if not previous["boxes"] or not (caption.get("wordFigure") or
                _figure_number(caption["text"]) in _FIG_NUMBER.findall(re.sub(r"\s+", "", previous["text"]))):
            continue
        before, label = previous["boxes"][-1], caption["boxes"]
        if before[0] != page or any(b[0] != page for b in label):
            continue
        info = section.pages[page]
        width, height = info["width"], info["height"]
        top = math.ceil((before[2] + before[4]) * height) + 2
        bottom = math.floor(min(b[2] for b in label) * height) - 1
        if bottom - top < 12:
            continue
        picture = s.get(m.TextbookFigure, (section.edition_id, info["image"]))
        ink = _ink_bounds(picture.png, top, bottom) if picture else None
        if not ink or ink[2] - ink[0] < 16 or ink[3] - ink[1] < 10:
            continue
        x0, x1, y0 = ink[0], ink[2], top + ink[1]
        if size := caption.get("wordFigure"):
            # 发布包使用 1.8 像素/磅。保留 Word 图框的大小，也容纳同一图中的可见标记。
            center = (x0 + x1) / 2
            x0, x1 = min(x0, center - .9 * size[0]), max(x1, center + .9 * size[0])
            y0 = max(top, min(y0, top + ink[3] - 1.8 * size[1]))
        left = max(0, min(x0 / width, *(b[1] for b in label)) - 5 / width)
        right = min(1, max(x1 / width, *(b[1] + b[3] for b in label)) + 5 / width)
        y = max(0, (y0 - 4) / height)
        end = min(1, max(b[2] + b[4] for b in label) + 5 / height)
        return [page, left, y, right - left, end - y]
    return None


def highlight(s, section, hit, figure_anchor=None):
    figure = hit.get("figure", False)
    boxes = hit["boxes"] if figure else _line_marks(hit["boxes"], section.pages)
    result = {"text": hit["text"], "figure": figure, "boxes": boxes}
    if figure_anchor is not None or figure:
        frame = _figure_frame(s, section, figure_anchor if figure_anchor is not None else hit["id"])
        if frame and (figure or figure_anchor == hit["id"]):
            result.update(figure=True, boxes=[frame])
        elif frame:
            result["frame"] = frame
    return result


def add_previews(s: Session, refs: list[dict]) -> None:
    """使用引文原版次的版面，兼容已保存的对话。"""
    by_section = {}
    for ref in refs:
        by_section.setdefault((ref["edition"], ref["section"]), []).append(ref)
    if not by_section:
        return
    section = m.TextbookSection
    for row in s.scalars(select(section).where(tuple_(section.edition_id, section.number).in_(list(by_section)))):
        anchors = {item["id"]: item for item in row.anchors}
        for ref in by_section[row.edition_id, row.number]:
            hit = anchors.get(ref["anchor"])
            if not hit or not hit["boxes"]:
                continue
            mark = highlight(s, row, hit, ref.get("figureAnchor"))
            previews = []
            marked_pages = [b[0] for b in mark["boxes"]]
            if mark.get("frame"):
                marked_pages.append(mark["frame"][0])
            for page_index in sorted(set(marked_pages)):
                page = row.pages[page_index]
                preview = {"page": page_index, "width": page["width"], "height": page["height"],
                           "figure": mark["figure"], "boxes": [b[1:] for b in mark["boxes"] if b[0] == page_index]}
                if (frame := mark.get("frame")) and frame[0] == page_index:
                    preview["frame"] = frame[1:]
                previews.append(preview)
            ref["preview"] = previews[0]
            if len(previews) > 1:
                ref["continuation"] = previews[1:]
