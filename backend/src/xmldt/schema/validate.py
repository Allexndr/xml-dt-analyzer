"""Version detection and XSD validation against a locally bundled schema set."""

from __future__ import annotations

import io
import json
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.parsers import expat

from lxml import etree

_SAFE_PART = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_EEC_URN = re.compile(r"urn:EEC:R:(\d{3}):([A-Za-z0-9_]{1,80}):v(\d{1,3}\.\d{1,3}\.\d{1,3})")
_lock = threading.Lock()


@dataclass
class SchemaInfo:
    code: str
    version: str
    namespace: str
    root_element: str
    supported: bool


@dataclass
class XsdError:
    line: int
    column: int
    message: str
    path: str | None


class _Stop(Exception):
    pass


def root_namespace(data: bytes) -> tuple[str, str] | None:
    """Return (namespace, local name) of the document element using expat; no regex on raw input.

    A national envelope (Keden ``<ccecd:KedenGoodsDeclaration>``) around an EEC document is
    skipped: the first EEC child of the root is returned instead."""
    found: list[tuple[str, str]] = []
    depth = [0]
    p = expat.ParserCreate(namespace_separator=" ")

    def start(name: str, _attrs) -> None:
        ns, _, local = name.rpartition(" ")
        depth[0] += 1
        if depth[0] == 1:
            found.append((ns, local))
            if _EEC_URN.fullmatch(ns):
                raise _Stop
            return
        if depth[0] == 2 and _EEC_URN.fullmatch(ns):
            found[0] = (ns, local)
            raise _Stop

    def end(_name: str) -> None:
        depth[0] -= 1

    def no_dtd(*_a) -> None:
        raise _Stop

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.StartDoctypeDeclHandler = no_dtd
    try:
        # Root start tag is normally near the beginning; feed in chunks until found.
        for i in range(0, min(len(data), 1 << 20), 1 << 16):
            p.Parse(data[i : i + (1 << 16)], False)
    except (_Stop, expat.ExpatError):
        pass
    return found[0] if found else None


def detect(data: bytes, schemas_dir: Path) -> SchemaInfo | None:
    """Detect EEC structure code and version from the root namespace URN."""
    root = root_namespace(data)
    if not root:
        return None
    ns, local = root
    m = _EEC_URN.fullmatch(ns)
    if not m:
        return None
    code, version = f"R.{m.group(1)}", m.group(3)
    supported = False
    if _SAFE_PART.fullmatch(code) and _SAFE_PART.fullmatch(version) and ".." not in version:
        bundle = (schemas_dir / code / version / "manifest.json").resolve()
        supported = bundle.is_relative_to(schemas_dir.resolve()) and bundle.exists()
    return SchemaInfo(code, version, ns, local, supported)


@lru_cache(maxsize=8)
def _schema(bundle_dir: str) -> etree.XMLSchema:
    d = Path(bundle_dir)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.XMLSchema(etree.parse(str(d / m["entry"]), parser))


_NS_DECL = re.compile(rb'xmlns(?::[A-Za-z0-9_.-]{1,40})?\s*=\s*"(urn:EEC:[^"]{1,200})"')
_TNS = re.compile(rb'targetNamespace\s*=\s*"(urn:EEC:[^"]{1,200})"')


@lru_cache(maxsize=8)
def _bundle_namespaces(bundle_dir: str) -> frozenset[str]:
    out = set()
    for f in Path(bundle_dir).rglob("*.xsd"):
        out.update(m.decode() for m in _TNS.findall(f.read_bytes()[:4096]))
    return frozenset(out)


def foreign_namespaces(data: bytes, bundle_dir: Path) -> list[str]:
    """EEC namespaces declared in the file's head that the bundle does not define (version mismatch)."""
    head = data[:16384]
    known = _bundle_namespaces(str(bundle_dir))
    return sorted({m.decode() for m in _NS_DECL.findall(head)} - known)


def _document_element(doc):
    """The EEC document: the root itself, or its first EEC child inside a national envelope."""
    root = doc.getroot()
    if _EEC_URN.fullmatch(etree.QName(root).namespace or ""):
        return doc
    for child in root:
        if isinstance(child.tag, str) and _EEC_URN.fullmatch(etree.QName(child).namespace or ""):
            return etree.ElementTree(child)
    return doc


def validate(data: bytes, bundle_dir: Path, max_errors: int = 200) -> list[XsdError]:
    """Validate against the bundle. Line numbers are libxml2's (lone CR is not a line break).

    The UI should map errors to editor coordinates via the node index (by ``path``).
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    doc = etree.parse(io.BytesIO(data), parser)
    target = _document_element(doc)
    schema = _schema(str(bundle_dir))
    with _lock:  # XMLSchema.error_log is per-instance state
        schema.validate(target)
        errors = list(schema.error_log)[:max_errors]
    return [XsdError(line=e.line, column=e.column, message=e.message, path=e.path) for e in errors]
