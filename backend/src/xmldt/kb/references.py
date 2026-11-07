"""Reference data outside Decision No. 378: the EAEU unit classifier and the TN VED EAEU code list.

* Units — Decision of the EEC Collegium No. 145 of 27.10.2020 «Классификатор единиц измерения
  и счета ЕАЭС» (consolidated text, edition on a date). Two lookups are built: by numeric code
  (attribute ``measurementUnitCode``) and by letter symbol (``MeasureUnitAbbreviationCode``, e.g. «КГ»).
* TN VED EAEU — 10-digit codes with names and tariff rates. The official source is the EEC
  (Decision of the EEC Council No. 80 of 14.09.2021); this loader reads any XLSX/CSV export with
  «Код» and «Наименование» columns. The bundled file is a third-party export (tws.by) and must be
  reconciled with the official edition before production use.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from .alta import cell_text, load, resolve_on_date
from .classifiers import Classifier, Item


def build_units(alta_html: Path, on: date) -> list[Classifier]:
    doc, log = resolve_on_date(load(str(alta_html)), on)
    by_code: list[Item] = []
    by_abbr: list[Item] = []
    section = None
    for t in doc.xpath(".//table"):
        for tr in t.xpath(".//tr"):
            cells = [cell_text(td) for td in tr.xpath("./td")]
            non_empty = [c for c in cells if c]
            if len(non_empty) == 1 and not re.fullmatch(r"\d{3}", non_empty[0]):
                if not non_empty[0].startswith("("):
                    section = non_empty[0]
                continue
            if len(cells) < 5 or not re.fullmatch(r"\d{3}", cells[0] or ""):
                continue
            code, intl, _mk, letters, name_ru = cells[:5]
            excluded = bool(re.match(r"^\s*исключ", name_ru, re.I))
            extra = {"международный код": intl, "буквенное обозначение": letters}
            if len(cells) > 6 and cells[6]:
                extra["международное обозначение"] = cells[6]
            by_code.append(Item(code=code, name=name_ru, extra=extra, section=section, excluded=excluded))
            for abbr in [a.strip() for a in re.split(r"[;,]", letters or "") if a.strip()]:
                by_abbr.append(Item(code=abbr, name=name_ru, extra={"код": code}, section=section, excluded=excluded))
    src = {"doc": "Решение Коллегии ЕЭК № 145 от 27.10.2020 (классификатор единиц измерения и счета ЕАЭС)",
           "as_of": str(on), "text_source": "alta.ru consolidated text",
           "ambiguous_editions": sum(r.ambiguous for r in log)}
    return [
        Classifier("units_eaeu", "Классификатор единиц измерения и счета ЕАЭС", ["Код", "Наименование"], 0, by_code, src),
        Classifier("units_eaeu_abbr", "Классификатор единиц измерения и счета ЕАЭС (буквенные обозначения)",
                   ["Обозначение", "Наименование"], 0, by_abbr, src),
    ]


def _rows(path: Path):
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            yield from csv.reader(f, delimiter=";" if ";" in f.readline() else ",")
        return
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = next((w for w in wb.worksheets if w.title.upper().replace(" ", "") in ("ТНВЭД", "TNVED")), wb.worksheets[-1])
    for row in ws.iter_rows(values_only=True):
        yield ["" if v is None else str(v) for v in row]


def build_tnved(path: Path, source: str, as_of: str) -> Classifier:
    items: list[Item] = []
    header: list[str] | None = None
    for row in _rows(path):
        if header is None:
            if row and any(str(c).strip().lower() == "код" for c in row):
                header = [str(c).strip().lower() for c in row]
            continue
        code = re.sub(r"\D", "", row[header.index("код")] if "код" in header else row[0])
        if len(code) != 10:
            continue
        name = row[header.index("наименование")] if "наименование" in header else row[1]
        name = re.sub(r"\s*🠺\s*", " → ", str(name)).strip()
        extra = {}
        if "тариф" in header and row[header.index("тариф")]:
            extra["ставка"] = row[header.index("тариф")]
        items.append(Item(code=code, name=name, extra=extra))
    return Classifier("tnved_eaeu", "ТН ВЭД ЕАЭС", ["Код", "Наименование"], 0, items,
                      {"doc": "ТН ВЭД ЕАЭС (Решение Совета ЕЭК № 80 от 14.09.2021)", "as_of": as_of,
                       "text_source": source,
                       "warning": "сторонняя выгрузка; перед продуктивным использованием сверить с официальной редакцией ЕТТ ЕАЭС"})
