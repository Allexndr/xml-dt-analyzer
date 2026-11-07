"""Extract the requisite table (table 3) of EEC structure decisions.

Table 3 of Decision No. 75 lists every requisite of the R.055 structure with
its hierarchy, model identifier, data type, multiplicity and a note that
carries format-level filling rules ("реквизит должен быть заполнен",
"реквизит не заполняется", conditions on procedures, etc.).

Hierarchy is encoded by leading empty indentation cells; numbering inside
nested type blocks uses ``*.`` (relative to the enclosing requisite), so the
indentation, not the number, is the source of truth for the tree.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from lxml import html

from .alta import cell_text

_NAME = re.compile(r"^(?P<num>[\d*][\d.*]*?)\.?\s+(?P<title>.+?)\s*\((?P<qname>[a-z]+:\s*[A-Za-z0-9_\s]+)\)\s*$", re.S)


@dataclass
class Requisite:
    row: int
    depth: int
    number: str
    title: str
    qname: str  # prefix:LocalName
    local_name: str
    path: str  # /GoodsDeclaration/... by local names
    description: str
    model_id: str
    data_type: str
    multiplicity: str
    note: str


def parse_table(table: html.HtmlElement, root_name: str = "GoodsDeclaration") -> tuple[list[Requisite], list[dict]]:
    out: list[Requisite] = []
    problems: list[dict] = []
    names: dict[str, str] = {}  # full number -> local name
    base = ""  # last absolute number; "*" in relative numbers refers to it
    for i, tr in enumerate(table.xpath(".//tr")):
        cells = [cell_text(td) for td in tr.xpath("./td|./th")]
        rest = [c for c in cells if c]
        if not rest:
            continue
        m = _NAME.match(rest[0])
        if not m:
            if i > 1 and re.match(r"^[\d*]", rest[0]):
                problems.append({"row": i, "cells": rest[:2]})
            continue
        # empty trailing cells (e.g. no note) were dropped above; re-pad from the right
        lead = len(cells) - len(cells[cells.index(rest[0]):])
        tail = cells[lead + 1:]
        tail = tail + [""] * max(0, 5 - len(tail))
        desc, model_id, dtype, mult, note = tail[:5]
        num = m.group("num").rstrip(".")
        if num.startswith("*"):
            if not base:
                problems.append({"row": i, "cells": rest[:2], "error": "relative number without base"})
                continue
            full = base + num[1:]
        else:
            full = num
            base = num
        segs = full.split(".")
        qname = re.sub(r"\s+", "", m.group("qname"))
        local = qname.split(":", 1)[1]
        names[full] = local
        chain = []
        for k in range(1, len(segs) + 1):
            key = ".".join(segs[:k])
            if key not in names:
                problems.append({"row": i, "error": f"missing parent {key} for {full}"})
                break
            chain.append(names[key])
        else:
            out.append(
                Requisite(
                    row=i,
                    depth=len(segs),
                    number=full,
                    title=" ".join(m.group("title").split()),
                    qname=qname,
                    local_name=local,
                    path="/" + root_name + "/" + "/".join(chain),
                    description=desc,
                    model_id=model_id,
                    data_type=dtype,
                    multiplicity=mult,
                    note=note,
                )
            )
    return out, problems


def to_dicts(reqs: list[Requisite]) -> list[dict]:
    return [asdict(r) for r in reqs]
