"""Single-pass XML indexer built on pyexpat with exact source coordinates."""

from __future__ import annotations

import sys

import codecs
import re
from array import array
from bisect import bisect_right
from collections.abc import Iterable
from xml.parsers import expat

from .models import IndexError_, IndexResult, Node
from .positions import line_col_from_offset, utf16_len

DEFAULT_GOODS_ITEM_NAMES = ("GDGoodsItemDetails",)

_DECL_RE = re.compile(rb"""^\s*<\?xml\s[^>]*?encoding\s*=\s*(["'])([A-Za-z][A-Za-z0-9._-]*)\1""")
_TAG_END_RE = re.compile(rb"""(?:[^>"']+|"[^"]*"|'[^']*')*>""")
_BREAK_RE = re.compile(rb"\r\n|\r|\n")
_WS = b" \t\r\n"
_CDATA_OPEN = b"<![CDATA["

_EXPAT_RU = {
    "XML_ERROR_SYNTAX": "синтаксическая ошибка",
    "XML_ERROR_NO_ELEMENTS": "корневой элемент не найден (документ пуст или оборван)",
    "XML_ERROR_INVALID_TOKEN": "недопустимый символ или конструкция",
    "XML_ERROR_UNCLOSED_TOKEN": "незакрытая конструкция",
    "XML_ERROR_PARTIAL_CHAR": "оборванный символ в конце данных",
    "XML_ERROR_TAG_MISMATCH": "закрывающий тег не соответствует открывающему",
    "XML_ERROR_DUPLICATE_ATTRIBUTE": "повторяющийся атрибут",
    "XML_ERROR_JUNK_AFTER_DOC_ELEMENT": "лишние данные после корневого элемента",
    "XML_ERROR_UNDEFINED_ENTITY": "неопределённая сущность",
    "XML_ERROR_UNBOUND_PREFIX": "необъявленный префикс пространства имён",
    "XML_ERROR_UNCLOSED_CDATA_SECTION": "незакрытая секция CDATA",
    "XML_ERROR_BAD_CHAR_REF": "недопустимая символьная ссылка",
    "XML_ERROR_ATTRIBUTE_EXTERNAL_ENTITY_REF": "внешняя сущность в атрибуте",
    "XML_ERROR_RECURSIVE_ENTITY_REF": "рекурсивная сущность",
    "XML_ERROR_XML_DECL": "ошибка в XML-декларации",
    "XML_ERROR_MISPLACED_XML_PI": "XML-декларация не в начале документа",
}


class _Abort(Exception):
    """Raised from expat handlers to stop parsing with a structured error."""

    def __init__(self, kind: str, message: str, byte_pos: int) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.byte_pos = byte_pos


def _fail(kind: str, message: str, line: int = 1, column: int = 1, offset: int = 0, **kw) -> IndexResult:
    err: IndexError_ = {
        "message": message,
        "line": line,
        "column": column,
        "offset_utf16": offset,
        "kind": kind,
    }
    return IndexResult(ok=False, error=err, **kw)


_EEC_DOC_NS = "urn:EEC:R:"


def _is_envelope_child(parent_ns: str, ns: str) -> bool:
    return ns.startswith(_EEC_DOC_NS) and not parent_ns.startswith(_EEC_DOC_NS)


def _detect(data: bytes) -> tuple[str, str, bool, int]:
    """Return (reported_encoding, python_codec, has_bom, bom_length)."""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8", "utf-8", True, 3
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", "utf-16-le", True, 2
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be", "utf-16-be", True, 2
    if data[:4] == b"<\x00?\x00":
        return "utf-16-le", "utf-16-le", False, 0
    if data[:4] == b"\x00<\x00?":
        return "utf-16-be", "utf-16-be", False, 0
    m = _DECL_RE.match(data[:1024])
    if m:
        label = m.group(2).decode("ascii").lower()
        return label, label, False, 0
    return "utf-8", "utf-8", False, 0


def _convert(buf: bytes, offsets: array, ascii_only: bool) -> array:
    """Map byte offsets (``-1`` = absent) into UTF-16 offsets of the decoded text."""
    if ascii_only:
        return offsets
    out = array("q", [-1]) * len(offsets)
    idx = [i for i, b in enumerate(offsets) if b >= 0]
    idx.sort(key=offsets.__getitem__)
    pb = pu = 0
    for i in idx:
        b = offsets[i]
        if b != pb:
            seg = buf[pb:b]
            pu += len(seg) if seg.isascii() else len(seg.decode("utf-8").encode("utf-16-le")) // 2
            pb = b
        out[i] = pu
    return out


def _trim(buf: bytes, a: int, b: int) -> tuple[int, int]:
    raw = buf[a:b]
    lead = len(raw) - len(raw.lstrip(_WS))
    if lead == len(raw):
        return a, a
    return a + lead, b - (len(raw) - len(raw.rstrip(_WS)))


def index_xml(
    data: bytes,
    *,
    max_bytes: int = 200_000_000,
    max_depth: int = 256,
    max_nodes: int = 5_000_000,
    goods_item_local_names: Iterable[str] = DEFAULT_GOODS_ITEM_NAMES,
) -> IndexResult:
    """Parse ``data`` once and index every element with exact UTF-16 coordinates.

    DOCTYPE/DTD/entity declarations are rejected; nothing is resolved externally.
    Never raises on bad input: failures are returned as ``IndexResult(ok=False, error=...)``.
    """
    if len(data) > max_bytes:
        return _fail("limit", f"Файл слишком большой: {len(data)} байт (лимит {max_bytes} байт).")

    encoding, codec, has_bom, bom_len = _detect(data)
    try:
        info = codecs.lookup(codec)
        # Reject non-text codecs such as hex/base64/zlib/rot13 that lookup() accepts.
        if not getattr(info, "_is_text_encoding", True):
            raise LookupError(codec)
    except LookupError:
        return _fail("encoding", f"Неизвестная кодировка в XML-декларации: {encoding}.", encoding=encoding)
    try:
        text = data[bom_len:].decode(codec)
        buf = text.encode("utf-8")
    except UnicodeError as exc:
        pos = getattr(exc, "start", 0)
        prefix = data[bom_len : bom_len + pos].decode(codec, "replace")
        line, col = line_col_from_offset(prefix, utf16_len(prefix))
        return _fail(
            "encoding",
            f"Не удалось декодировать файл в кодировке {encoding}: недопустимые данные в позиции {pos} байт.",
            line,
            col,
            utf16_len(prefix),
            encoding=encoding,
            has_bom=has_bom,
        )

    goods_names = frozenset(goods_item_local_names)
    ascii_only = len(buf) == len(text)
    nodes: list[Node] = []
    sb = array("q")
    eb = array("q")
    vsb = array("q")
    veb = array("q")
    name_cache: dict[str, tuple[str, str, str]] = {}
    spath_cache: dict[tuple[str, str], str] = {}
    # Frame: [node, child_counters|None, text_parts|None, is_empty_tag, tag_end_byte]
    stack: list[list] = []

    parser = expat.ParserCreate("utf-8", " ")
    parser.namespace_prefixes = True
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def on_start(name: str, attrs: dict[str, str]) -> None:
        pos = parser.CurrentByteIndex
        if len(stack) >= max_depth:
            raise _Abort("limit", f"Превышена максимальная глубина вложенности ({max_depth}).", pos)
        if len(nodes) >= max_nodes:
            raise _Abort("limit", f"Превышено максимальное число элементов ({max_nodes}).", pos)
        parts = name_cache.get(name)
        if parts is None:
            p = name.split(" ")
            if len(p) == 1:
                parts = ("", p[0], "")
            elif len(p) == 2:
                parts = (p[0], p[1], "")
            else:
                parts = (p[0], p[1], p[2])
            name_cache[name] = parts
        ns, local, prefix = parts
        m = _TAG_END_RE.match(buf, pos)
        tag_end = m.end() if m else pos
        empty = buf[tag_end - 2 : tag_end] == b"/>"
        if attrs:
            attrs = {(k.split(" ")[1] if " " in k else k): v for k, v in attrs.items()}
        if stack:
            parent = stack[-1]
            pnode: Node = parent[0]
            pnode.child_count += 1
            parent[2] = None  # parent is not a leaf: drop its text
            if parent[1] is None:
                parent[1] = {}
            n = parent[1].get(local, 0) + 1
            parent[1][local] = n
            key = (pnode.spath, local)
            spath = spath_cache.get(key)
            if spath is None:
                if pnode.parent_id is None and _is_envelope_child(pnode.ns, ns):
                    # national envelope (e.g. Keden <KedenGoodsDeclaration>) around an EEC document:
                    # schema paths start at the EEC element so rules and concepts apply unchanged
                    spath = spath_cache[key] = sys.intern(f"/{local}")
                else:
                    spath = spath_cache[key] = sys.intern(f"{pnode.spath}/{local}")
            goods_no = n if local in goods_names else pnode.goods_no
            pid: int | None = pnode.node_id
        else:
            n = 1
            pnode = None
            spath = sys.intern(f"/{local}")
            goods_no = 1 if local in goods_names else None
            pid = None
        node = Node(len(nodes), pid, len(stack), ns, local, prefix, spath, n, pnode, attrs, None, 0, goods_no)
        nodes.append(node)
        sb.append(pos)
        eb.append(-1)
        vsb.append(-1)
        veb.append(-1)
        stack.append([node, None, [], empty, tag_end])

    def on_end(name: str) -> None:
        pos = parser.CurrentByteIndex
        frame = stack.pop()
        node: Node = frame[0]
        nid = node.node_id
        if frame[3]:
            eb[nid] = frame[4]
        else:
            eb[nid] = buf.index(b">", pos) + 1
        if node.child_count == 0:
            val = "".join(frame[2] or ()).strip()
            node.value = val
            if val and not frame[3]:
                a, b = _trim(buf, frame[4], pos)
                raw = buf[a:b]
                if raw.startswith(_CDATA_OPEN) and raw.endswith(b"]]>") and raw.count(_CDATA_OPEN) == 1:
                    a, b = _trim(buf, a + len(_CDATA_OPEN), b - 3)
                vsb[nid], veb[nid] = a, b

    def on_chars(s: str) -> None:
        if stack:
            parts = stack[-1][2]
            if parts is not None:
                parts.append(s)

    def on_doctype(*_a) -> None:
        raise _Abort(
            "doctype",
            "Объявление DOCTYPE (DTD) запрещено по соображениям безопасности.",
            parser.CurrentByteIndex,
        )

    def on_entity_decl(*_a) -> None:
        raise _Abort("doctype", "Объявления сущностей (ENTITY) запрещены.", parser.CurrentByteIndex)

    def on_ext_ref(*_a) -> int:
        raise _Abort("doctype", "Внешние сущности запрещены.", parser.CurrentByteIndex)

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CharacterDataHandler = on_chars
    parser.StartDoctypeDeclHandler = on_doctype
    parser.EntityDeclHandler = on_entity_decl
    parser.ExternalEntityRefHandler = on_ext_ref

    def error_at(kind: str, message: str, byte_pos: int) -> IndexResult:
        pre = buf[:byte_pos].decode("utf-8", "ignore")
        off = utf16_len(pre)
        line, col = line_col_from_offset(text, off)
        return _fail(kind, message, line, col, off, text=text, encoding=encoding, has_bom=has_bom)

    try:
        parser.Parse(buf, True)
    except _Abort as exc:
        return error_at(exc.kind, exc.message, exc.byte_pos)
    except expat.ExpatError as exc:
        code_name = expat.errors.codes.get(exc.code, "")
        key = next((k for k in _EXPAT_RU if getattr(expat.errors, k, None) == expat.errors.messages.get(exc.code)), "")
        ru = _EXPAT_RU.get(key) or expat.errors.messages.get(exc.code, code_name)
        return error_at("syntax", f"XML не well-formed: {ru}.", parser.ErrorByteIndex)

    # Post-pass: convert byte offsets to UTF-16 offsets and compute lines.
    s16 = _convert(buf, sb, ascii_only)
    e16 = _convert(buf, eb, ascii_only)
    vs16 = _convert(buf, vsb, ascii_only)
    ve16 = _convert(buf, veb, ascii_only)
    line_starts = [0]
    line_starts.extend(m.end() for m in _BREAK_RE.finditer(buf))
    for i, node in enumerate(nodes):
        node.start = s16[i]
        node.end = e16[i]
        if vs16[i] >= 0:
            node.value_start = vs16[i]
            node.value_end = ve16[i]
        node.line_start = bisect_right(line_starts, sb[i])
        node.line_end = bisect_right(line_starts, eb[i] - 1)

    return IndexResult(ok=True, text=text, encoding=encoding, has_bom=has_bom, nodes=nodes)
