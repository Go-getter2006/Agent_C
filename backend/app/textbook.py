"""C 语言教材的私有发布包、检索和引文标记。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m

BUNDLE_VERSION = 2
_DIGEST = re.compile(r"[0-9a-f]{64}")
_WORDS = re.compile(r"[\u3400-\u9fff]+|%[a-z]+|[a-z_]\w*|[0-9]+(?:[.][0-9]+)+|&&|\|\||[&*]", re.ASCII)
_STOP = set("什么 怎么 如何 一下 是不是 为什么 这个 那个 有什么 给我 帮我 有哪些 是什么 和例 解释 区别 想问 能否 能不".split())
_QUESTION_WORDS = re.compile(r"为什么|是什么|这是什么|怎么|如何|帮我|解释一下|请问")
_DEFINITION = re.compile(r"^\s*(?:请问\s*)?(?:什么是\s*(.+?)|(.+?)\s*(?:是什么|是什么意思))\s*[？?。]*$")
_FIGURE_QUESTION = re.compile(r"图\s*\d|流程图|示意图|图解|这张图|上面的图|插图")
_FOLLOWUP = re.compile(r"^(?:那|那么|那要是|这时|还是|也要|可以不|还有呢|再解释)")


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
            for anchor in section.anchors:
                words = _terms(anchor["text"])
                if not words or not anchor["boxes"]:
                    continue
                ref = {"section": section.number, "anchor": anchor["id"], "path": list(path)}
                passages.append(Passage(ref, anchor["text"], words, title_words, anchor.get("figure", False)))
                frequency.update(words)
        _cache[edition.digest] = passages, frequency
        if len(_cache) > 2:
            _cache.popitem(last=False)
        return passages, frequency


def retrieve(s: Session, course: str, question: str, history: list[tuple[str, str]]) -> dict:
    """只检索本轮问到的教材片段。沿用本轮答复调用，无需单独请求模型。"""
    if course != "C" or not (query := _terms(_QUESTION_WORDS.sub(" ", question))) or (edition := active(s)) is None:
        return {}
    followup = _FOLLOWUP.search(question.strip())
    previous = next((text for role, text in reversed(history) if role == "student"), "") if followup else ""
    remembered = _terms(previous) - query
    passages, frequency = _passages(s, edition)
    weights = {term: math.log(1 + len(passages) / (1 + frequency[term])) for term in query | remembered}
    wants_figure = bool(_FIGURE_QUESTION.search(question))
    definition = _DEFINITION.fullmatch(question)
    term = (definition[1] or definition[2]).strip() if definition else ""
    ranked = []
    for passage in passages:
        if passage.figure and not wants_figure:
            continue
        in_text = query & passage.words
        carried = remembered & passage.words
        if not in_text and len(carried) < 2:
            continue
        if len(query) >= 3 and len((passage.words | passage.title_words) & query) < 2 and len(carried) < 2:
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
    ranked.sort(key=lambda item: item[0], reverse=True)
    return {f"T{i}": (item.text, dict(item.reference, edition=edition.id), item.figure)
            for i, (_, item) in enumerate(ranked[:5], 1)}


def prompt(matches: dict) -> str:
    if not matches:
        return ""
    lines = [f"[{key}] {' › '.join(ref['path'])}（{'图题' if figure else '原文'}）\n{text}"
             for key, (text, ref, figure) in matches.items()]
    return "【本轮检索到的教材资料】\n" + "\n\n".join(lines)


def citations(matches: dict, selected) -> list[dict]:
    if not isinstance(selected, list):
        return []
    keys = dict.fromkeys(key for key in selected if isinstance(key, str) and key in matches)
    return [matches[key][1] for key in list(keys)[:2]]
