"""Deterministic fact tools over an indexed declaration.

Every tool returns a ``ToolResult`` whose numbers and values come from the
index and whose ``evidence`` points at exact source ranges. The LLM layer only
chooses tools and phrases answers; it never computes these values itself.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from xmldt.indexer import IndexResult, Node
from xmldt.kb.concepts import Catalog

_IDX = re.compile(r"\[\d+\]")
MAX_EVIDENCE_PER_FINDING = 50


def safe_int(v: str | None) -> int | None:
    """ASCII-digit integer or None (never raises on malformed declarations)."""
    if v is None:
        return None
    v = v.strip()
    return int(v) if v.isascii() and v.isdigit() else None
ITEM = "/GoodsDeclaration/GDGoodsShipmentDetails/GDGoodsItemDetails"
MARK = ITEM + "/DTIdentificationMeansDetails"


@dataclass
class Evidence:
    node_id: int
    line_start: int
    line_end: int
    start: int
    end: int
    tag: str
    label: str


@dataclass
class ToolResult:
    tool: str
    value: dict
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Declaration:
    """Index plus lookups by schema path (path without positional indexes)."""

    def __init__(self, idx: IndexResult, catalog: Catalog):
        if not idx.ok:
            raise ValueError("cannot analyse a document that failed to index")
        self.idx = idx
        self.catalog = catalog
        self.nodes = idx.nodes
        self.by_spath: dict[str, list[Node]] = defaultdict(list)
        self.children: dict[int, list[Node]] = defaultdict(list)
        for n in self.nodes:
            self.by_spath[n.spath].append(n)
            if n.parent_id is not None:
                self.children[n.parent_id].append(n)

    # ---- helpers ------------------------------------------------------
    def ev(self, n: Node, label: str) -> Evidence:
        return Evidence(n.node_id, n.line_start, n.line_end, n.start, n.end, n.qname, label)

    def child(self, n: Node, local: str) -> Node | None:
        return next((c for c in self.children[n.node_id] if c.local_name == local), None)

    def childs(self, n: Node, local: str) -> list[Node]:
        return [c for c in self.children[n.node_id] if c.local_name == local]

    def descendants(self, n: Node, local: str) -> list[Node]:
        out, stack = [], [n]
        while stack:
            cur = stack.pop()
            for c in self.children[cur.node_id]:
                if c.local_name == local:
                    out.append(c)
                stack.append(c)
        return sorted(out, key=lambda x: x.node_id)

    def items(self) -> list[Node]:
        return self.by_spath.get(ITEM, [])

    # ---- tools --------------------------------------------------------
    def get_concept(self, key: str, goods_no: int | None = None) -> ToolResult:
        c = self.catalog.concepts.get(key)
        if c is None:
            raise KeyError(f"unknown concept {key}")
        found = [n for p in c.paths for n in self.by_spath.get(p, []) if goods_no is None or n.goods_no == goods_no]
        values = []
        evidence = []
        for n in found:
            leaves = [n] if n.value is not None else [d for d in self._leaves(n)]
            values.append({
                "goods_no": n.goods_no,
                "element": n.qname,
                "fields": [{"element": lf.local_name, "value": lf.value, "attrs": lf.attrs, "node_id": lf.node_id}
                           for lf in leaves],
            })
            evidence.append(self.ev(n, c.title + (f", товар № {n.goods_no}" if n.goods_no else "")))
        res = ToolResult("get_concept", {"concept": key, "title": c.title, "graphs": c.graphs,
                                         "found": len(found), "values": values}, evidence)
        if not found:
            res.warnings.append(f"В файле нет сведений «{c.title}».")
        for n in found:
            eq = self.child(n, "EqualIndicator")
            if eq is not None:
                res.warnings.append(
                    f"{c.title}: указан признак совпадения сведений (EqualIndicator = {eq.value}); "
                    "реквизиты лица могут быть приведены в другой графе.")
                res.evidence.append(self.ev(eq, "Признак совпадения сведений"))
        return res

    def _leaves(self, n: Node) -> list[Node]:
        out, stack = [], list(reversed(self.children[n.node_id]))
        while stack:
            cur = stack.pop()
            if cur.value is not None:
                out.append(cur)
            stack.extend(reversed(self.children[cur.node_id]))
        return out

    def count_goods(self) -> ToolResult:
        items = self.items()
        declared = self.by_spath.get("/GoodsDeclaration/GoodsQuantity", [])
        declared_n = safe_int(declared[0].value) if declared else None
        res = ToolResult("count_goods", {"goods_items": len(items), "declared_goods_quantity": declared_n})
        for it in items:
            ordn = self.child(it, "ConsignmentItemOrdinal")
            desc = " ".join(d.value or "" for d in self.childs(it, "GoodsDescriptionText"))
            res.evidence.append(self.ev(it, f"Товар № {ordn.value if ordn else it.goods_no}: {desc[:80]}"))
            if ordn is not None and ordn.value != str(it.goods_no):
                res.warnings.append(f"Порядковый номер товара {ordn.value} не совпадает с его позицией в файле ({it.goods_no}).")
        if declared:
            res.evidence.append(self.ev(declared[0], "Графа 5: заявленное количество товаров"))
            if declared_n is None:
                res.warnings.append(f"Графа 5 заполнена некорректно: «{declared[0].value}» не является числом.")
            elif declared_n != len(items):
                res.warnings.append(f"В графе 5 заявлено товаров: {declared[0].value}; товарных позиций в файле: {len(items)}.")
        return res

    @staticmethod
    def _code_string(units: list[tuple[str | None, str | None]]) -> str:
        return "".join(f"({ai}){v}" if ai else (v or "") for ai, v in units)

    def _code_of(self, item_details: Node | None) -> str | None:
        if item_details is None:
            return None
        units = []
        for du in self.childs(item_details, "IdentificationMeansDataUnitDetails"):
            ai = self.child(du, "AIId")
            v = self.child(du, "IdentificationMeansUnitCharacterValueId")
            units.append((ai.value if ai else None, v.value if v else None))
        return self._code_string(units)

    def marking_stats(self) -> ToolResult:
        """Marking (identification means) per goods item, without guessing."""
        per_item = []
        evidence: list[Evidence] = []
        warnings: list[str] = []
        all_codes: Counter[str] = Counter()
        totals = Counter()
        for it in self.items():
            no = it.goods_no
            ordn = self.child(it, "ConsignmentItemOrdinal")
            label_no = ordn.value if ordn else str(no)
            sign = self.child(it, "CIMSignCode")
            dt = self.child(it, "DTIdentificationMeansDetails")
            row = {"goods_no": label_no, "marking_sign": sign.value if sign else None, "has_marking_data": dt is not None}
            if dt is None:
                row.update(listed_codes=0, unique_codes=0, ranges=[], aggregated_ids=0, declared_quantity=None)
                per_item.append(row)
                if sign is not None:
                    warnings.append(f"Товар № {label_no}: признак маркировки «{sign.value}» указан, но сведений о кодах в файле нет "
                                    "(это не обязательно ошибка: возможна маркировка после выпуска — нужна проверка условий).")
                    evidence.append(self.ev(sign, f"Товар № {label_no}: признак маркировки"))
                continue
            kind = self.child(dt, "InformationKindCode")
            after = self.child(dt, "CIMMarkingCode")
            qty = self.child(dt, "CIMQuantity")
            code_nodes = [x for x in self.descendants(dt, "IdentificationMeansItemDetails")
                          if x.parent_id is not None and self.nodes[x.parent_id].local_name == "IdentificationMeansListDetails"]
            codes = [self._code_of(x) or "" for x in code_nodes]
            nodes_by_code: dict[str, list[Node]] = defaultdict(list)
            for c, x in zip(codes, code_nodes):
                nodes_by_code[c].append(x)
            dup = {c: k for c, k in Counter(codes).items() if k > 1}
            ranges = []
            for rg in self.descendants(dt, "IdentificationMeansRangeDetails"):
                first = self._code_of(self.child(rg, "FirstIdentificationMeansItemDetails"))
                last = self._code_of(self.child(rg, "LastIdentificationMeansItemDetails"))
                if first is None or last is None:
                    warnings.append(f"Товар № {label_no}: у диапазона кодов не указана граница — размер диапазона неизвестен.")
                size = _range_size(first, last)
                ranges.append({"first": first, "last": last, "size": size,
                               "size_status": "computed" if size is not None else "unknown"})
                evidence.append(self.ev(rg, f"Товар № {label_no}: диапазон кодов {first} … {last}"))
            atis = self.descendants(dt, "IdentificationMeansCustomsId")
            for a in atis:
                evidence.append(self.ev(a, f"Товар № {label_no}: агрегированный таможенный идентификатор"))
            row.update(
                information_kind=kind.value if kind else None,
                marking_after_release=after.value if after else None,
                listed_codes=len(codes), unique_codes=len(set(codes)),
                duplicates_within_item=dup, ranges=ranges, aggregated_ids=len(atis),
                declared_quantity=safe_int(qty.value) if qty is not None else None,
            )
            per_item.append(row)
            all_codes.update(codes)
            totals["listed"] += len(codes)
            totals["range_codes"] += sum(r["size"] or 0 for r in ranges)
            totals["unknown_ranges"] += sum(1 for r in ranges if r["size"] is None)
            totals["aggregated_ids"] += len(atis)
            evidence.append(self.ev(dt, f"Товар № {label_no}: сведения о маркировке"))
            for c, k in dup.items():
                warnings.append(f"Товар № {label_no}: код {c} повторяется {k} раза.")
                for x in nodes_by_code[c][:MAX_EVIDENCE_PER_FINDING]:
                    evidence.append(self.ev(x, f"Товар № {label_no}: повторяющийся код {c}"))
            if qty is not None and row["declared_quantity"] is None:
                warnings.append(f"Товар № {label_no}: заявленное количество кодов «{qty.value}» не является числом.")
            if qty is not None and row["declared_quantity"] is not None and not ranges and not atis \
                    and row["declared_quantity"] != len(codes):
                warnings.append(f"Товар № {label_no}: заявлено {qty.value} кодов, перечислено {len(codes)}.")
                evidence.append(self.ev(qty, f"Товар № {label_no}: заявленное количество кодов"))
        cross_dups = {c: k for c, k in all_codes.items() if k > 1}
        with_marking = [r["goods_no"] for r in per_item if r["has_marking_data"]]
        without = [r["goods_no"] for r in per_item if not r["has_marking_data"]]
        value = {
            "goods_items": len(per_item),
            "items_with_marking_data": len(with_marking),
            "items_with_marking_data_list": with_marking,
            "items_without_marking_data_list": without,
            "codes_listed": totals["listed"],
            "codes_listed_unique": len(all_codes),
            "codes_in_ranges": totals["range_codes"],
            "ranges_with_unknown_size": totals["unknown_ranges"],
            "aggregated_customs_ids": totals["aggregated_ids"],
            "duplicate_codes": cross_dups,
            "per_item": per_item,
            "note": "Коды в диапазонах и за агрегированными идентификаторами не перечислены в файле поштучно; "
                    "их число вычислено по границам диапазона либо неизвестно.",
        }
        return ToolResult("marking_stats", value, evidence, warnings)


def _range_size(first: str | None, last: str | None) -> int | None:
    """Size of a range whose bounds differ only in a trailing number; None if not derivable."""
    if not first or not last or len(first) != len(last):
        return None
    i = 0
    while i < len(first) and first[i] == last[i]:
        i += 1
    j = i
    while j > 0 and first[j - 1].isdigit():
        j -= 1  # include shared leading digits of the numeric tail
    a, b = first[j:], last[j:]
    if not (a.isdigit() and b.isdigit()) or int(b) < int(a):
        return None
    return int(b) - int(a) + 1
