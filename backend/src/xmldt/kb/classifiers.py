"""Classifiers of Decision of the Customs Union Commission No. 378, as of a date.

The consolidated Alta-Soft text splits a classifier into many table fragments
(each amended fragment is its own table), so a classifier is the run of
tables between one "КЛАССИФИКАТОР …" heading and the next. The first row of the
first table is the header; later fragments carry data rows only. Single-cell
rows are section titles. Rows whose name starts with "Исключен" record codes
(or code ranges like "01012 - 01016") that were removed and must not be accepted.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from lxml import html

from .alta import Resolution, cell_text, load, resolve_on_date

# Stable keys for the classifiers used by the analyzer. Matching is by a heading prefix.
KNOWN = {
    "ВИДОВ ТАМОЖЕННЫХ ПРОЦЕДУР": "customs_procedures",
    "ОСОБЕННОСТЕЙ ПЕРЕМЕЩЕНИЯ ТОВАРОВ": "goods_move_features",
    "ВИДОВ ТРАНСПОРТА И ТРАНСПОРТИРОВКИ ТОВАРОВ": "transport_modes",
    "МЕТОДОВ ОПРЕДЕЛЕНИЯ ТАМОЖЕННОЙ СТОИМОСТИ": "valuation_methods",
    "РЕЗУЛЬТАТОВ ТАМОЖЕННОГО КОНТРОЛЯ ТАМОЖЕННОЙ СТОИМОСТИ": "valuation_control_results",
    "ОСОБЕННОСТЕЙ ТАМОЖЕННОГО ДЕКЛАРИРОВАНИЯ ТОВАРОВ": "declaration_features",
    "ЛЬГОТ ПО УПЛАТЕ ТАМОЖЕННЫХ ПЛАТЕЖЕЙ": "payment_preferences",
    "ВИДОВ ДОКУМЕНТОВ И СВЕДЕНИЙ": "document_kinds",
    "ВИДОВ НАЛОГОВ, СБОРОВ И ИНЫХ ПЛАТЕЖЕЙ": "payment_kinds",
    "ОСОБЕННОСТЕЙ УПЛАТЫ ТАМОЖЕННЫХ И ИНЫХ ПЛАТЕЖЕЙ": "payment_features",
    "СПОСОБОВ УПЛАТЫ ТАМОЖЕННЫХ И ИНЫХ ПЛАТЕЖЕЙ": "payment_methods",
    "ВИДОВ ГРУЗА, УПАКОВКИ И УПАКОВОЧНЫХ МАТЕРИАЛОВ": "package_kinds",
    "УСЛОВИЙ ПОСТАВКИ": "delivery_terms",
    "РЕШЕНИЙ, ПРИНИМАЕМЫХ ТАМОЖЕННЫМИ ОРГАНАМИ": "customs_decisions",
    "ЕДИНИЦ ИЗМЕРЕНИЯ": "units",
    "ВИДОВ СПЕЦИАЛЬНЫХ УПРОЩЕНИЙ": "special_simplifications",
    "СПОСОБОВ ОБЕСПЕЧЕНИЯ ИСПОЛНЕНИЯ ОБЯЗАННОСТИ": "guarantee_methods",
    "ВИДОВ ПЕРЕМЕЩЕНИЯ ТОВАРОВ В СООТВЕТСТВИИ С ТАМОЖЕННОЙ ПРОЦЕДУРОЙ ТАМОЖЕННОГО ТРАНЗИТА": "transit_move_kinds",
    "ДОПОЛНИТЕЛЬНЫХ ХАРАКТЕРИСТИК И ПАРАМЕТРОВ": "payment_parameters",
    "СТРАН МИРА": "countries",
    "ВАЛЮТ": "currencies",
    "МЕСТ НАХОЖДЕНИЯ ТОВАРОВ": "goods_locations",
    "ТИПОВ ТРАНСПОРТНЫХ СРЕДСТВ МЕЖДУНАРОДНОЙ ПЕРЕВОЗКИ": "vehicle_types",
    "МАРОК ДОРОЖНЫХ ТРАНСПОРТНЫХ СРЕДСТВ": "vehicle_makes",
    "КАТЕГОРИЙ ТОВАРОВ, КОТОРЫЕ МОГУТ БЫТЬ ЗАЯВЛЕНЫ К ВЫПУСКУ": "pre_declaration_release_categories",
    "СЛУЧАЕВ НЕПРЕДОСТАВЛЕНИЯ ОБЕСПЕЧЕНИЯ": "guarantee_exemptions",
    "СВЕДЕНИЙ ОБ ОБЪЕКТАХ ИНТЕЛЛЕКТУАЛЬНОЙ СОБСТВЕННОСТИ": "ip_objects",
    "ДЛИН КОНТЕЙНЕРОВ": "container_lengths",
    "ШИРИН И ВЫСОТ КОНТЕЙНЕРОВ": "container_widths_heights",
    "ТИПОВ КОНТЕЙНЕРОВ": "container_types",
}

_HEADING = re.compile(r"^КЛАССИФИКАТОР\s+(.+)$", re.I | re.S)
_EXCLUDED = re.compile(r"^\s*(исключен|утратил)", re.I)
_AMEND_NOTE = re.compile(r"\s*\((в ред\.|введен[аоы]?|исключен[аоы]?)[^)]*\)\s*$", re.I | re.S)
# Per-classifier column roles: (alt-code header prefix, name header prefix)
ROLES = {"currencies": ("буквенный код", "полное наименование")}
_RANGE = re.compile(r"^\s*(\w+)\s*[-–]\s*(\w+)\s*$")


@dataclass
class Item:
    code: str
    name: str
    extra: dict[str, str] = field(default_factory=dict)
    section: str | None = None
    excluded: bool = False
    alt_code: str | None = None  # e.g. alphabetic currency code


@dataclass
class Classifier:
    key: str
    title: str
    columns: list[str]
    code_column: int
    items: list[Item]
    source: dict

    def lookup(self, code: str) -> Item | None:
        idx = self.__dict__.get("_index")
        if idx is None:  # built once; large lists (ТН ВЭД, 13k codes) must not be scanned per check
            idx = {}
            for i in self.items:
                idx.setdefault(i.code, i)
                if i.alt_code:
                    idx.setdefault(i.alt_code, i)
            self.__dict__["_index"] = idx
        return idx.get(code.strip())

    def lookup_ci(self, code: str) -> Item | None:
        c = code.strip().upper()
        return next((i for i in self.items if i.code.upper() == c or (i.alt_code or "").upper() == c), None)


def _key_for(title: str) -> str | None:
    t = " ".join(title.upper().split())
    # longest prefix wins ("ВИДОВ ПЕРЕМЕЩЕНИЯ … ТРАНЗИТА" before shorter keys)
    for prefix in sorted(KNOWN, key=len, reverse=True):
        if t.startswith(prefix):
            return KNOWN[prefix]
    return None


def _code_column(header: list[str]) -> int:
    for i, h in enumerate(header):
        h = h.lower()
        if h.startswith("код") or "цифровой код" in h:
            return i
    return 0


def _expand_range(a: str, b: str) -> list[str] | None:
    if a.isdigit() and b.isdigit() and len(a) == len(b) and int(b) >= int(a) and int(b) - int(a) < 1000:
        return [str(n).zfill(len(a)) for n in range(int(a), int(b) + 1)]
    return None


def parse_classifiers(doc: html.HtmlElement) -> list[Classifier]:
    groups: list[tuple[str, list[html.HtmlElement]]] = []
    for el in doc.iter():
        if el.tag in ("div", "p") and el.get("class") in ("ordw-center", "ordw-right"):
            t = " ".join(el.text_content().split())
            m = _HEADING.match(t)
            if m:
                groups.append((m.group(1), []))
        elif el.tag == "table" and groups:
            groups[-1][1].append(el)
    out: list[Classifier] = []
    for raw_title, tables in groups:
        key = _key_for(raw_title)
        if not key or not tables:
            continue
        core = re.sub(r"\s*\((в ред\.|введен).*$", "", raw_title, flags=re.S).strip()
        title = "Классификатор " + core.lower()
        rows = [[cell_text(td) for td in tr.xpath("./td|./th")] for t in tables for tr in t.xpath(".//tr")]
        rows = [r for r in rows if any(r)]
        header = rows[0]
        cc = _code_column(header)
        items: list[Item] = []
        section = None
        for r in rows[1:]:
            cells = [c for c in r]
            if len([c for c in cells if c]) == 1 and len(cells) <= 2 and not re.match(r"^[\dA-Z]{1,6}$", cells[0] or ""):
                section = next(c for c in cells if c)
                continue
            if len(cells) <= cc or not cells[cc]:
                continue
            code_raw = " ".join(cells[cc].split())
            others = [c for i, c in enumerate(cells) if i != cc]
            name = others[0] if others else ""
            alt = None
            if key in ROLES:
                hl = [h.lower().replace("\n", " ") for h in header]
                ai = next((i for i, h in enumerate(hl) if h.startswith(ROLES[key][0])), None)
                ni = next((i for i, h in enumerate(hl) if h.startswith(ROLES[key][1])), None)
                alt = cells[ai] if ai is not None and ai < len(cells) else None
                name = cells[ni] if ni is not None and ni < len(cells) else name
            name = _AMEND_NOTE.sub("", " ".join(name.split()))
            extra = {header[i] if i < len(header) else f"col{i}": c for i, c in enumerate(cells) if i not in (cc,) and c and c != name}
            excluded = bool(_EXCLUDED.match(name))
            rng = _RANGE.match(code_raw)
            codes = (_expand_range(rng.group(1), rng.group(2)) if rng else None) or [code_raw]
            for code in codes:
                items.append(Item(code=code, name=name, extra=extra, section=section, excluded=excluded, alt_code=alt))
        out.append(Classifier(key=key, title=title, columns=header, code_column=cc, items=items, source={}))
    return out


def build(alta_html: Path, on: date) -> tuple[list[Classifier], list[Resolution]]:
    doc, log = resolve_on_date(load(str(alta_html)), on)
    cls = parse_classifiers(doc)
    for c in cls:
        c.source = {"doc": "Решение Комиссии Таможенного союза № 378", "as_of": str(on),
                    "text_source": "alta.ru consolidated text"}
    return cls, log


def save(cls: list[Classifier], out: Path) -> None:
    out.write_text(json.dumps({c.key: asdict(c) for c in cls}, ensure_ascii=False, indent=1), encoding="utf-8")


def load_saved(path: Path) -> dict[str, Classifier]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: Classifier(**{**v, "items": [Item(**i) for i in v["items"]]}) for k, v in raw.items()}


def load_all(derived: Path) -> dict[str, Classifier]:
    """Latest Decision-378 classifiers merged with reference classifiers (units, ТН ВЭД)."""
    out: dict[str, Classifier] = {}
    for pattern in ("classifiers_378_*.json", "classifiers_ref_*.json"):
        files = sorted(derived.glob(pattern))
        if files:
            out.update(load_saved(files[-1]))
    return out
