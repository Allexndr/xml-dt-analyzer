"""Edition-aware reader for consolidated legal texts published by Alta-Soft.

Alta-Soft consolidated documents mark every amended fragment with a block::

    div.ordw-urobr
      div.new | div.old          -- header: "Нов. ред." / "См. пред. ред." + amending act
      div.content
        div.content-new | div.content-old
          p > span.ordw-comment  -- "Начало действия редакции - 01.10.2026 г." / "Редакция действует до …"
          …edition body…
          div.ordw-urobr         -- the previous edition (recursively)

``resolve_on_date`` replaces every such block with the edition in force on a
given date, so the result can be parsed as a plain document "as of" that date.
Each resolution is recorded so answers can cite the edition that was used.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date

from lxml import html

_DATE = r"(\d{2})\.(\d{2})\.(\d{4})"
_FROM = [
    re.compile(r"Начало действия редакции\s*-\s*" + _DATE),
    re.compile(r"вступает в силу с\s*" + _DATE),
]
_TO = [re.compile(r"Редакция действует до\s*" + _DATE)]

# Entry-into-force dates of amending acts (href -> date), see kb/acts.py. Filled by the KB builder.
ACT_DATES: dict = {}


def _d(m: re.Match) -> date | None:
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:  # impossible dates in source text, e.g. 31.02.2026
        return None


@dataclass
class Edition:
    body: html.HtmlElement  # div.content-new / div.content-old
    amending_act: str  # header text, e.g. "Решение 90 от 07.10.2025 Коллегии ЕЭК"
    valid_from: date | None
    valid_to: date | None
    comments: list[str] = field(default_factory=list)
    undated: bool = False  # start date unknown and not derivable from the chain
    act_href: str | None = None
    dated_by_act: bool = False  # valid_from taken from the amending act's general entry-into-force date

    def in_force(self, on: date) -> bool:
        return (self.valid_from is None or self.valid_from <= on) and (self.valid_to is None or on <= self.valid_to)


@dataclass
class Resolution:
    amending_act: str
    valid_from: date | None
    valid_to: date | None
    chain_length: int
    fallback: bool  # True when no edition range contained the date
    ambiguous: bool = False  # selection relied on an undated edition; needs human confirmation


def _cls(el: html.HtmlElement) -> str:
    return el.get("class") or ""


def _is_old(block: html.HtmlElement) -> bool:
    """A nested block is the previous edition when its header is "См. пред. ред." (class old)."""
    header = next((c for c in block if _cls(c) in ("new", "old")), None)
    return header is not None and _cls(header) == "old"


def _edition_chain(block: html.HtmlElement) -> list[Edition]:
    """Return editions newest-first for one div.ordw-urobr block."""
    chain: list[Edition] = []
    cur: html.HtmlElement | None = block
    while cur is not None:
        header = next((c for c in cur if _cls(c) in ("new", "old")), None)
        content = next((c for c in cur if _cls(c) == "content"), None)
        body = None
        if content is not None:
            body = next((c for c in content if _cls(c).startswith("content-")), None)
        if body is None:
            break
        comments = [" ".join(s.split()) for s in body.xpath("./p/span[@class='ordw-comment']/text()")]
        joined = " ".join(comments)
        vf = next((d for rx in _FROM if (m := rx.search(joined)) and (d := _d(m))), None)
        vt = next((d for rx in _TO if (m := rx.search(joined)) and (d := _d(m))), None)
        act = " ".join(header.text_content().split()) if header is not None else ""
        href = (header.xpath("./a/@href") or [None])[0] if header is not None else None
        act = re.sub(r"^(Нов\. ред\.|См\. пред\. ред\.)\s*", "", act)
        ed = Edition(body=body, amending_act=act, valid_from=vf, valid_to=vt, comments=comments, act_href=href)
        # the header links the act that established this edition (for both "Нов. ред." and "См. пред. ред.")
        if ed.valid_from is None and href in ACT_DATES:
            ed.valid_from, ed.dated_by_act = ACT_DATES[href], True
        chain.append(ed)
        cur = next((c for c in body if c.tag == "div" and _cls(c) == "ordw-urobr" and _is_old(c)), None)
    # Infer missing valid_to of older editions from the next newer edition's start.
    for newer, older in zip(chain, chain[1:]):
        if older.valid_to is None and newer.valid_from is not None:
            older.valid_to = date.fromordinal(newer.valid_from.toordinal() - 1)
        if newer.valid_from is None and older.valid_to is not None:
            newer.valid_from = date.fromordinal(older.valid_to.toordinal() + 1)
    # An edition with no dates at all that has an older sibling is ambiguous:
    # its start date lives in the amending act, not in the markup.
    for newer, older in zip(chain, chain[1:]):
        if newer.valid_from is None and older.valid_to is None:
            newer.undated = True
            newer.comments.append("UNDATED: start date must be taken from the amending act")
    return chain


def _materialize(ed: Edition) -> list:
    """Children of the edition body without the comment paragraph(s) and the nested older edition."""
    out = []
    for c in ed.body:
        if c.tag == "div" and _cls(c) == "ordw-urobr" and _is_old(c):
            continue  # previous edition; nested "new" blocks are amendments inside this edition
        if c.tag == "p" and c.xpath("./span[@class='ordw-comment']"):
            continue
        out.append(copy.deepcopy(c))
    return out


def resolve_on_date(root: html.HtmlElement, on: date) -> tuple[html.HtmlElement, list[Resolution]]:
    """Return a copy of the document with every edition block resolved for ``on``."""
    doc = copy.deepcopy(root)
    log: list[Resolution] = []
    while True:
        blocks = doc.xpath("//div[@class='ordw-urobr'][not(ancestor::div[@class='ordw-urobr'])]")
        if not blocks:
            break
        for block in blocks:
            chain = _edition_chain(block)
            if not chain:
                block.drop_tree()
                continue
            # Newest edition whose known range contains the date. If its start is unknown
            # (undated), or the boundary with the next older edition is unknown, the
            # choice depends on the amending act's entry-into-force date: flag it.
            pos = next((i for i, e in enumerate(chain) if e.in_force(on)), None)
            chosen = chain[pos] if pos is not None else None
            ambiguous = chosen is not None and (
                chosen.undated or (chosen.valid_from is None and pos + 1 < len(chain))
                or (pos > 0 and chain[pos - 1].undated)
            )
            fallback = chosen is None
            if chosen is None:
                starts = [e.valid_from for e in chain if e.valid_from]
                before_all = bool(starts) and on < min(starts)
                chosen = chain[-1] if before_all else chain[0]
            log.append(Resolution(chosen.amending_act, chosen.valid_from, chosen.valid_to, len(chain), fallback, ambiguous))
            parent = block.getparent()
            idx = parent.index(block)
            tail = block.tail
            parent.remove(block)
            inserted = _materialize(chosen)
            for i, c in enumerate(inserted):
                parent.insert(idx + i, c)
            if tail:
                # inline text that followed the block must stay after the inserted content
                if inserted:
                    last = inserted[-1]
                    last.tail = (last.tail or "") + tail
                elif idx > 0:
                    prev = parent[idx - 1]
                    prev.tail = (prev.tail or "") + tail
                else:
                    parent.text = (parent.text or "") + tail
    return doc, log


def load(path: str) -> html.HtmlElement:
    with open(path, "rb") as f:
        doc = html.fromstring(f.read())
    for bad in doc.xpath("//script|//style|//img"):
        bad.drop_tree()
    body = doc.xpath("//div[contains(@class,'jTamdocBody')]")
    return body[0] if body else doc


def cell_text(el: html.HtmlElement) -> str:
    el = copy.deepcopy(el)
    for node in el.iter("p", "div", "br", "li"):
        if node is not el:
            node.tail = "\n" + (node.tail or "")
    text = el.text_content().replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)
