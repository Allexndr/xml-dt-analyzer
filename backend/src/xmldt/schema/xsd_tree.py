"""Flatten an EEC XSD bundle into a tree of element declarations.

The EEC schemas declare almost every element globally in data-model schemas
(``csdo``, ``casdo``, ``ccdo``, ``cacdo``) and reference them with ``ref``.
This module resolves refs, named types, extensions and restrictions and emits
one ``SchemaNode`` per reachable element path, starting from the root element.

Each node keeps the data-model identifier (e.g. ``M.CA.CDE.00180``) taken from
the comment that precedes the global declaration, so nodes can later be joined
with the requisite table of Decision No. 75 (table 3).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import etree

XS = "http://www.w3.org/2001/XMLSchema"
_ID_COMMENT = re.compile(r"^\s*(M\.[A-Z.]+\.\d+)\s*:\s*(.*?)\s*$", re.S)


def _q(tag: str) -> str:
    return f"{{{XS}}}{tag}"


@dataclass
class Facets:
    patterns: list[str] = field(default_factory=list)
    enumerations: list[str] = field(default_factory=list)
    length: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    total_digits: int | None = None
    fraction_digits: int | None = None
    min_inclusive: str | None = None
    max_inclusive: str | None = None


@dataclass
class AttrDecl:
    name: str
    type: str | None
    required: bool
    doc: str | None


@dataclass
class SchemaNode:
    path: str  # /GoodsDeclaration/DeclarantDetails/...
    ns: str
    name: str
    prefix: str
    depth: int
    min_occurs: int
    max_occurs: int | None  # None = unbounded
    model_id: str | None  # M.CA.CDE.00180 etc.
    model_title: str | None
    doc: str | None  # documentation (local override first, then global)
    type: str | None  # {ns}TypeName or None for anonymous
    type_model_id: str | None
    is_leaf: bool
    base_type: str | None = None  # closest named base (for simple content)
    facets: Facets | None = None
    attributes: list[AttrDecl] = field(default_factory=list)
    in_choice: bool = False


class SchemaSet:
    """Loads all XSD files of a bundle and indexes global components."""

    def __init__(self, files: list[Path]):
        parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True)
        self.elements: dict[str, etree._Element] = {}
        self.types: dict[str, etree._Element] = {}
        self.ids: dict[str, tuple[str, str]] = {}  # qname -> (model_id, title)
        self.prefixes: dict[str, str] = {}
        for f in files:
            root = etree.parse(str(f), parser).getroot()
            tns = root.get("targetNamespace", "")
            for p, uri in root.nsmap.items():
                if p and uri == tns:
                    self.prefixes.setdefault(uri, p)
            pending: tuple[str, str] | None = None
            for child in root:
                if isinstance(child, etree._Comment):
                    m = _ID_COMMENT.match(child.text or "")
                    pending = (m.group(1), m.group(2)) if m else None
                    continue
                name = child.get("name")
                if not name:
                    pending = None
                    continue
                qn = f"{{{tns}}}{name}"
                if child.tag == _q("element"):
                    self.elements[qn] = child
                elif child.tag in (_q("complexType"), _q("simpleType")):
                    self.types[qn] = child
                if pending:
                    self.ids[qn] = pending
                pending = None

    @staticmethod
    def resolve_qname(el: etree._Element, value: str) -> str:
        if ":" in value:
            p, local = value.split(":", 1)
            return f"{{{el.nsmap.get(p, '')}}}{local}"
        return f"{{{el.nsmap.get(None, '')}}}{value}"


def _doc(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    d = el.find(f"{_q('annotation')}/{_q('documentation')}")
    if d is not None and d.text:
        return " ".join(d.text.split())
    return None


def _occurs(el: etree._Element) -> tuple[int, int | None]:
    mn = int(el.get("minOccurs", "1"))
    mx_raw = el.get("maxOccurs", "1")
    return mn, None if mx_raw == "unbounded" else int(mx_raw)


def _split(qn: str) -> tuple[str, str]:
    ns, local = qn[1:].split("}", 1)
    return ns, local


class TreeBuilder:
    def __init__(self, ss: SchemaSet, max_depth: int = 40):
        self.ss = ss
        self.max_depth = max_depth
        self.nodes: list[SchemaNode] = []

    # ---- type analysis -------------------------------------------------
    def _collect_facets(self, type_qn: str | None, inline: etree._Element | None) -> tuple[Facets, str | None]:
        """Walk simple-type restriction chain collecting facets (nearest first wins for scalar facets)."""
        facets = Facets()
        base_named: str | None = None
        seen: set[str] = set()
        node = inline if inline is not None else (self.ss.types.get(type_qn) if type_qn else None)
        while node is not None:
            restr = node.find(_q("restriction"))
            if restr is None:
                sc = node.find(_q("simpleContent"))
                if sc is not None:
                    restr = sc.find(_q("restriction"))
                    if restr is None:
                        restr = sc.find(_q("extension"))
            if restr is None:
                break
            for f in restr:
                if not isinstance(f.tag, str):
                    continue
                tag = etree.QName(f).localname
                v = f.get("value")
                if tag == "pattern":
                    facets.patterns.append(v)
                elif tag == "enumeration":
                    facets.enumerations.append(v)
                else:
                    attr = {
                        "length": "length",
                        "minLength": "min_length",
                        "maxLength": "max_length",
                        "totalDigits": "total_digits",
                        "fractionDigits": "fraction_digits",
                        "minInclusive": "min_inclusive",
                        "maxInclusive": "max_inclusive",
                    }.get(tag)
                    if attr and getattr(facets, attr) is None:
                        setattr(facets, attr, int(v) if attr.endswith(("length", "digits")) else v)
            base = restr.get("base")
            if not base:
                break
            bq = self.ss.resolve_qname(restr, base)
            if bq.startswith(f"{{{XS}}}"):
                base_named = base_named or bq
                break
            if bq in seen:
                break
            seen.add(bq)
            base_named = base_named or bq
            node = self.ss.types.get(bq)
        return facets, base_named

    def _attributes(self, ctype: etree._Element, seen: tuple[str, ...] = ()) -> list[AttrDecl]:
        """Attributes declared by this complex type itself (not by nested inline child types)."""
        holders = [ctype]
        for content in ("simpleContent", "complexContent"):
            c = ctype.find(_q(content))
            if c is not None:
                for deriv in ("extension", "restriction"):
                    d = c.find(_q(deriv))
                    if d is not None:
                        holders.append(d)
                        base = self.ss.resolve_qname(d, d.get("base", ""))
                        if base in self.ss.types and base not in seen:
                            bt = self.ss.types[base]
                            if bt.tag == _q("complexType"):
                                # both extension and restriction inherit base attributes;
                                # a restriction may redeclare (override) some of them
                                inherited = self._attributes(bt, seen + (base,))
                                own = self._own_attrs(holders)
                                names = {a.name for a in own}
                                return [a for a in inherited if a.name not in names] + own
        return self._own_attrs(holders)

    def _own_attrs(self, holders: list[etree._Element]) -> list[AttrDecl]:
        out: list[AttrDecl] = []
        for h in holders:
            for a in h.findall(_q("attribute")):
                name = a.get("name") or (a.get("ref") or "").split(":")[-1]
                t = a.get("type")
                out.append(AttrDecl(name=name, type=self.ss.resolve_qname(a, t) if t else None,
                                    required=a.get("use") == "required", doc=_doc(a)))
        return out

    def _particles(self, ctype: etree._Element, seen_types: tuple[str, ...]) -> list[tuple[etree._Element, bool, int, int | None]]:
        """Child element particles in order (base first for extensions).

        Each item: (element, in_choice, group_min_factor, group_max_factor) where the factors
        come from enclosing sequence/choice/all occurs; a choice makes its members optional.
        """
        out: list[tuple[etree._Element, bool, int, int | None]] = []
        cc = ctype.find(_q("complexContent"))
        holder = ctype
        if cc is not None:
            ext = cc.find(_q("extension"))
            restr = cc.find(_q("restriction"))
            deriv = ext if ext is not None else restr
            if deriv is not None:
                base = self.ss.resolve_qname(deriv, deriv.get("base"))
                if ext is not None and base in self.ss.types and base not in seen_types:
                    out.extend(self._particles(self.ss.types[base], seen_types + (base,)))
                holder = deriv

        def mul(a: int | None, b: int | None) -> int | None:
            return None if a is None or b is None else a * b

        def walk(group: etree._Element, in_choice: bool, fmin: int, fmax: int | None) -> None:
            for p in group:
                if not isinstance(p.tag, str):
                    continue
                if p.tag == _q("element"):
                    out.append((p, in_choice, fmin, fmax))
                elif p.tag in (_q("sequence"), _q("all"), _q("choice")):
                    gmin, gmax = _occurs(p)
                    is_choice = p.tag == _q("choice")
                    walk(p, in_choice or is_choice, 0 if is_choice else fmin * gmin, mul(fmax, gmax))

        walk(holder, False, 1, 1)
        return out

    # ---- tree expansion ------------------------------------------------
    def build(self, root_qn: str) -> list[SchemaNode]:
        decl = self.ss.elements[root_qn]
        self._emit(decl, None, "", 0, (1, 1), (), False)
        return self.nodes

    def _emit(
        self,
        local_decl: etree._Element,
        global_qn: str | None,
        parent_path: str,
        depth: int,
        occurs: tuple[int, int | None],
        seen_types: tuple[str, ...],
        in_choice: bool,
    ) -> None:
        ref = local_decl.get("ref")
        if ref:
            global_qn = self.ss.resolve_qname(local_decl, ref)
            decl = self.ss.elements.get(global_qn)
            if decl is None:
                raise KeyError(f"Unresolved element ref {global_qn}")
        else:
            decl = local_decl
            if global_qn is None:
                tns = decl.getroottree().getroot().get("targetNamespace", "")
                global_qn = f"{{{tns}}}{decl.get('name')}"
        ns, name = _split(global_qn)
        path = f"{parent_path}/{name}"
        type_attr = decl.get("type")
        type_qn = self.ss.resolve_qname(decl, type_attr) if type_attr else None
        type_el = self.ss.types.get(type_qn) if type_qn else None
        inline = decl.find(_q("complexType"))
        if inline is None:
            inline = decl.find(_q("simpleType"))
        ctype = inline if inline is not None else type_el

        is_complex_with_children = False
        particles: list[tuple[etree._Element, bool, int, int | None]] = []
        attrs: list[AttrDecl] = []
        if ctype is not None and ctype.tag == _q("complexType"):
            particles = self._particles(ctype, seen_types)
            is_complex_with_children = bool(particles)
            attrs = self._attributes(ctype)

        facets = base = None
        if not is_complex_with_children:
            facets, base = self._collect_facets(type_qn, inline)

        mid = self.ss.ids.get(global_qn, (None, None))
        tmid = self.ss.ids.get(type_qn, (None, None)) if type_qn else (None, None)
        self.nodes.append(
            SchemaNode(
                path=path,
                ns=ns,
                name=name,
                prefix=self.ss.prefixes.get(ns, ""),
                depth=depth,
                min_occurs=occurs[0],
                max_occurs=occurs[1],
                model_id=mid[0],
                model_title=mid[1],
                doc=_doc(local_decl) or _doc(decl),
                type=type_qn,
                type_model_id=tmid[0],
                is_leaf=not is_complex_with_children,
                base_type=base,
                facets=facets,
                attributes=attrs,
                in_choice=in_choice,
            )
        )
        if not is_complex_with_children or depth >= self.max_depth:
            return
        if type_qn and type_qn in seen_types:
            return  # recursive type: stop expansion
        child_seen = seen_types + ((type_qn,) if type_qn else ())
        for p, ch, fmin, fmax in particles:
            emin, emax = _occurs(p)
            occ = (emin * fmin, None if emax is None or fmax is None else emax * fmax)
            self._emit(p, None, path, depth + 1, occ, child_seen, ch)


def load_bundle(bundle_dir: Path) -> tuple[dict, list[SchemaNode]]:
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    files = [bundle_dir / f["file"] for f in manifest["files"]]
    ss = SchemaSet(files)
    root_qn = f"{{{manifest['namespace']}}}{manifest['root_element']}"
    return manifest, TreeBuilder(ss).build(root_qn)


def dump_tree(nodes: list[SchemaNode], out: Path) -> None:
    out.write_text(
        json.dumps([asdict(n) for n in nodes], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
