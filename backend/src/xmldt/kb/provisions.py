"""Provisions of Decision of the Customs Union Commission No. 257 (filling rules of the goods declaration).

The consolidated text is resolved for a date (see ``alta.resolve_on_date``) and split into
provisions — the unit an answer may cite:

* a numbered point of the Procedure («15. …») with its section (I–XII) or appendix;
* a sub-point of a point («31) графа 31. "Грузовые места и описание товаров"»), carrying the
  graph number it governs.

Search is lexical (crude Russian stemming + TF-IDF) with a strong boost for the graph number,
because users ask "графа 31", "ИМ 40" — numbers that vector search handles poorly.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .alta import cell_text, load, resolve_on_date

DOC = "Решение Комиссии Таможенного союза № 257"
_SECTION = re.compile(r"^([IVX]+)\.\s+(.+)$")
_APPENDIX = re.compile(r"^Приложение\s+N?\s*(\d+)\b", re.I)
_POINT = re.compile(r"^(\d{1,3})(?:\^(\d{1,2}))?\.\s+(.+)$")  # superscripts marked as ^N (point 41^2 = 41.2)
_SUB = re.compile(r"^(\d{1,3})(?:\^(\d{1,2}))?\)\s+(.+)$")
_GRAPH = re.compile(r"^граф[аы]\s+(\d{1,2}|[АБВ])\b\.?\s*[\"«]?([^\"»]*)", re.I)
_BOX = re.compile(r"[┌┐└┘│─├┤┬┴┼]")


@dataclass
class Provision:
    id: str  # "p15.31" | "a2.p3"
    ref: str  # human citation
    section: str | None
    point: str
    subpoint: str | None
    graph: str | None
    title: str
    text: str
    as_of: str
    ambiguous_edition: bool = False
    tokens: list[str] = field(default_factory=list, repr=False)


_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)


def stem(w: str) -> str:
    w = w.lower().replace("ё", "е")
    for suf in ("ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ией", "иях", "ах", "ях", "ов", "ев", "ей",
                "ой", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ам", "ям", "ом", "ем", "ую", "юю", "а", "я",
                "о", "е", "ы", "и", "у", "ю", "ь"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text) if len(w) > 1]


def _main(x: str | None) -> int:
    return int(x.split(".")[0]) if x else 0


def _next_point(cur: str | None, num: str, sup: str | None) -> bool:
    """A numbered line starts a new point only if numbering advances plausibly (lists inside
    a sub-point such as "1. …" must not reset the structure)."""
    n = int(num)
    if cur is None:
        return True
    c = _main(cur)
    return (n == c and sup is not None) or c < n <= c + 3


def _next_sub(cur: str | None, num: str) -> bool:
    n = int(num)
    return cur is None or _main(cur) < n <= _main(cur) + 3 or n == _main(cur)


def parse(lines: list[str], as_of: str, ambiguous: bool) -> list[Provision]:
    # skip the table of contents: the body starts at the second "I. Общие положения"
    starts = [i for i, l in enumerate(lines) if l.strip().startswith("I. Общие положения")]
    body = lines[starts[1] if len(starts) > 1 else 0:]
    out: list[Provision] = []
    section = appendix = None
    point = sub = graph = None
    buf: list[str] = []
    title = ""

    def flush():
        if point is None or not buf:
            return
        text = "\n".join(buf).strip()
        if not text:
            return
        pid = (f"a{appendix}." if appendix else "") + f"p{point}" + (f".{sub}" if sub else "")
        sup = lambda x: re.sub(r"\.(\d+)$", lambda mm: "".join("⁰¹²³⁴⁵⁶⁷⁸⁹"[int(c)] for c in mm.group(1)), x)  # noqa: E731
        ref = DOC + (f", приложение № {appendix}" if appendix else ", Порядок заполнения ДТ") + f", п. {sup(point)}" \
            + (f", подп. {sup(sub)}" if sub else "") + (f" (графа {graph})" if graph else "")
        out.append(Provision(pid, ref, section, point, sub, graph, title, text, as_of, ambiguous))

    for raw in body:
        line = raw.strip()
        if not line or _BOX.search(line):
            continue
        if m := _APPENDIX.match(line):
            flush()
            appendix, section, point, sub, graph, buf, title = m.group(1), None, None, None, None, [], ""
            continue
        if (m := _SECTION.match(line)) and len(line) < 160:
            flush()
            section, point, sub, graph, buf = f"{m.group(1)}. {m.group(2)}", None, None, None, []
            continue
        if (m := _POINT.match(line)) and _next_point(point, m.group(1), m.group(2)):
            flush()
            point = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
            sub, graph, title, buf = None, None, m.group(3)[:120], [line]
            continue
        if point is not None and (m := _SUB.match(line)) and _next_sub(sub, m.group(1)):
            flush()
            sub = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
            g = _GRAPH.match(m.group(3))
            graph = g.group(1).upper() if g else None
            title, buf = m.group(3)[:120], [line]
            continue
        if line.startswith(("См. также", "(в ред.", "(введен", "(абзац введен", "(пп. ", "(п. ")):
            continue  # publisher cross-references and amendment notes, not the norm itself
        buf.append(line.replace("^", ""))
    flush()
    for p in out:
        p.tokens = tokenize(p.title + " " + p.text)
    return out


def build(alta_html: Path, on: date) -> list[Provision]:
    doc, log = resolve_on_date(load(str(alta_html)), on)
    for sp in doc.xpath(".//span[contains(@style,'vertical-align: super')]"):
        if sp.text and sp.text.strip().isdigit():
            sp.text = "^" + sp.text.strip()  # keep point numbers like 41² distinguishable
    ambiguous = any(r.ambiguous for r in log)
    return parse(cell_text(doc).split("\n"), str(on), ambiguous)


def save(provs: list[Provision], path: Path) -> None:
    path.write_text(json.dumps([{k: v for k, v in asdict(p).items() if k != "tokens"} for p in provs],
                               ensure_ascii=False, indent=1), encoding="utf-8")


class NormIndex:
    def __init__(self, provs: list[Provision]):
        self.provs = provs
        for p in provs:
            if not p.tokens:
                p.tokens = tokenize(p.title + " " + p.text)
        n = len(provs)
        df = Counter(t for p in provs for t in set(p.tokens))
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.by_id = {p.id: p for p in provs}

    @staticmethod
    def load(path: Path) -> "NormIndex":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return NormIndex([Provision(**r) for r in raw])

    def search(self, query: str, graph: str | None = None, k: int = 5) -> list[tuple[float, Provision]]:
        q = tokenize(query)
        g = graph or (m.group(1).upper() if (m := re.search(r"граф\w*\s*(\d{1,2}|[абв])\b", query, re.I)) else None)
        scored = []
        for p in self.provs:
            tf = Counter(p.tokens)
            s = sum(self.idf.get(t, 0) * (1 + math.log(tf[t])) for t in set(q) if tf[t])
            s /= math.sqrt(len(p.tokens) + 20) / 6
            if g and p.graph == g:
                s += 8 + (4 if p.point == "15" else 0)  # the graph's own sub-point of the import procedure first
            elif g and re.search(rf"\bграф\w*\s+{re.escape(g)}\b", p.text, re.I):
                s += 2
            if s > 0:
                scored.append((s, p))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


def excerpt(p: Provision, query: str, max_chars: int = 1600) -> str:
    """The paragraphs of a provision most relevant to the query, in document order."""
    paras = [x for x in p.text.split("\n") if x.strip()]
    if sum(len(x) for x in paras) <= max_chars:
        return "\n".join(paras)
    q = set(tokenize(query))
    head = paras[0]
    scored = sorted(range(1, len(paras)), key=lambda i: -len(q & set(tokenize(paras[i]))))
    keep, size = {0}, len(head)
    for i in scored:
        if size + len(paras[i]) > max_chars:
            continue
        keep.add(i)
        size += len(paras[i])
    out = [paras[i] for i in sorted(keep)]
    return "\n".join(out) + "\n[…]"
