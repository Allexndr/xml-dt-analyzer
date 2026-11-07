"""Tests for the XML indexer."""

from __future__ import annotations

import codecs
import os
import re
import time
from pathlib import Path

import pytest

from xmldt.indexer import (
    index_xml,
    line_col_from_offset,
    offset_from_line_col,
    slice_utf16,
    utf16_len,
)

FIXTURE = Path(__file__).parent / "fixtures" / "indexer" / "goods_declaration.xml"
NS_ROOT = "urn:EEC:R:055:GoodsDeclaration:v1.2.0"
NS_CSDO = "urn:EEC:M:SimpleDataObjects:v0.4.16"
NS_CACDO = "urn:EEC:M:CA:ComplexDataObjects:v1.10.4"


def _base_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _variants() -> dict[str, bytes]:
    t = _base_text()
    minified = re.sub(r">\s+<", "><", t)
    return {
        "lf": t.encode("utf-8"),
        "bom": codecs.BOM_UTF8 + t.encode("utf-8"),
        "crlf": t.replace("\n", "\r\n").encode("utf-8"),
        "cp1251": t.replace('encoding="UTF-8"', 'encoding="windows-1251"').encode("cp1251"),
        "min": minified.encode("utf-8"),
        "utf16": codecs.BOM_UTF16_LE
        + t.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16-le"),
    }


def _logical(res):
    return [
        (n.node_id, n.parent_id, n.depth, n.ns, n.local_name, n.prefix, n.path, n.attrs,
         n.value, n.child_count, n.goods_no)
        for n in res.nodes
    ]


def test_fixture_basics():
    res = index_xml(_base_text().encode())
    assert res.ok and res.error is None
    root = res.nodes[0]
    assert (root.node_id, root.parent_id, root.depth) == (0, None, 0)
    assert root.ns == NS_ROOT and root.local_name == "GoodsDeclaration" and root.prefix == ""
    assert root.path == "/GoodsDeclaration[1]"
    by_path = {n.path: n for n in res.nodes}
    descr = by_path["/GoodsDeclaration[1]/GDGoodsShipmentDetails[1]/GDGoodsItemDetails[2]/GoodsDescriptionText[1]"]
    assert descr.ns == NS_CSDO and descr.prefix == "csdo"
    assert descr.value == "Груши зимние «Конференция»"
    assert descr.attrs == {"languageCode": "ru"}
    ship = by_path["/GoodsDeclaration[1]/GDGoodsShipmentDetails[1]"]
    assert ship.ns == NS_CACDO and ship.child_count == 4
    note = by_path["/GoodsDeclaration[1]/ConsignmentNote[1]".replace("/ConsignmentNote", "/GDGoodsShipmentDetails[1]/ConsignmentNote")]
    assert note.attrs == {"note": "a>b", "code": 'x"y'}
    assert by_path["/GoodsDeclaration[1]/Note[1]"].value == "a < b & c"
    assert by_path["/GoodsDeclaration[1]/Empty[1]"].value == ""
    assert by_path["/GoodsDeclaration[1]/EmptyPair[1]"].value == ""


def test_goods_no_and_paths():
    res = index_xml(_base_text().encode())
    items = [n for n in res.nodes if n.local_name == "GDGoodsItemDetails"]
    assert [n.goods_no for n in items] == [1, 2, 3]
    assert [n.path.rsplit("/", 1)[1] for n in items] == ["GDGoodsItemDetails[1]", "GDGoodsItemDetails[2]", "GDGoodsItemDetails[3]"]
    for n in res.nodes:
        in_item = "GDGoodsItemDetails[" in n.path
        assert (n.goods_no is not None) == in_item
        if in_item:
            k = int(re.search(r"GDGoodsItemDetails\[(\d+)\]", n.path).group(1))
            assert n.goods_no == k
    custom = index_xml(_base_text().encode(), goods_item_local_names=("GDGoodsShipmentDetails",))
    assert {n.goods_no for n in custom.nodes if n.local_name == "CommodityCode"} == {1}


@pytest.mark.parametrize("name", ["bom", "crlf", "cp1251", "min", "utf16"])
def test_equivalence(name):
    ref = index_xml(_variants()["lf"])
    res = index_xml(_variants()[name])
    assert ref.ok and res.ok, res.error
    assert _logical(res) == _logical(ref)
    assert res.has_bom == (name in ("bom", "utf16"))
    assert res.text.startswith("<")
    assert res.encoding == {"cp1251": "windows-1251", "utf16": "utf-16-le"}.get(name, "utf-8")


@pytest.mark.parametrize("name", ["lf", "bom", "crlf", "cp1251", "min", "utf16"])
def test_slices(name):
    res = index_xml(_variants()[name])
    assert res.ok
    for n in res.nodes:
        s = slice_utf16(res.text, n.start, n.end)
        assert s.startswith("<" + n.qname), (n.path, s[:30])
        assert s.endswith(">")
        if n.value:
            assert n.value_start is not None and n.value_end is not None
            assert slice_utf16(res.text, n.value_start, n.value_end).strip() == n.value
        else:
            assert n.value_start is None and n.value_end is None


def test_empty_element_range():
    res = index_xml(_base_text().encode())
    empty = next(n for n in res.nodes if n.local_name == "Empty")
    assert slice_utf16(res.text, empty.start, empty.end) == "<csdo:Empty/>"
    pair = next(n for n in res.nodes if n.local_name == "EmptyPair")
    assert slice_utf16(res.text, pair.start, pair.end) == "<csdo:EmptyPair></csdo:EmptyPair>"
    cdata = next(n for n in res.nodes if n.local_name == "Note")
    assert slice_utf16(res.text, cdata.value_start, cdata.value_end) == "a < b & c"


def test_known_lines_lf():
    res = index_xml(_base_text().encode())
    lines = _base_text().split("\n")
    for n in res.nodes:
        assert lines[n.line_start - 1].lstrip().startswith("<" + n.qname) or ("<" + n.qname) in lines[n.line_start - 1]
    root = res.nodes[0]
    assert (root.line_start, root.line_end) == (3, 26)
    items = [n for n in res.nodes if n.local_name == "GDGoodsItemDetails"]
    assert [(n.line_start, n.line_end) for n in items] == [(10, 14), (15, 19), (20, 24)]
    assert next(n for n in res.nodes if n.local_name == "Empty").line_start == 5


def test_lines_crlf_same_as_lf():
    a = index_xml(_variants()["lf"])
    b = index_xml(_variants()["crlf"])
    assert [(n.line_start, n.line_end) for n in a.nodes] == [(n.line_start, n.line_end) for n in b.nodes]


def test_lines_lone_cr():
    t = _base_text().replace("\n", "\r")
    a = index_xml(_base_text().encode())
    b = index_xml(t.encode())
    assert [(n.line_start, n.line_end) for n in a.nodes] == [(n.line_start, n.line_end) for n in b.nodes]


def test_minified_single_line():
    res = index_xml(_variants()["min"])
    assert res.ok and "\n" not in res.text.strip()
    assert all(n.line_start == 1 and n.line_end == 1 for n in res.nodes)


def test_non_bmp():
    xml = '<?xml version="1.0"?>\n<r><a>😀 Привет 😀</a><b>x</b></r>'
    res = index_xml(xml.encode())
    assert res.ok
    a, b = res.nodes[1], res.nodes[2]
    assert a.value == "😀 Привет 😀"
    assert slice_utf16(res.text, a.start, a.end) == "<a>😀 Привет 😀</a>"
    assert slice_utf16(res.text, a.value_start, a.value_end) == a.value
    assert a.end - a.start == len("<a>") + 2 + 1 + 6 + 1 + 2 + len("</a>")
    assert slice_utf16(res.text, b.start, b.end) == "<b>x</b>"
    assert b.start == a.end
    # The same document as UTF-16 input.
    res16 = index_xml(codecs.BOM_UTF16_BE + xml.encode("utf-16-be"))
    assert [(n.start, n.end) for n in res16.nodes] == [(n.start, n.end) for n in res.nodes]


def test_non_bmp_in_attribute_and_comment():
    xml = '<r a="😀"><!-- 😀 --><b x="1>2"/></r>'
    res = index_xml(xml.encode())
    b = res.nodes[1]
    assert slice_utf16(res.text, b.start, b.end) == '<b x="1>2"/>'


def test_entities_and_mixed():
    res = index_xml(b"<r><a>x &amp; y</a><m>t<c/>u</m></r>")
    a, m = res.nodes[1], res.nodes[2]
    assert a.value == "x & y"
    assert m.value is None and m.child_count == 1 and m.value_start is None


# ---- errors -------------------------------------------------------------


def test_malformed_mismatched_tag():
    res = index_xml(b"<a>\n  <b>x</c>\n</a>")
    assert not res.ok and res.error["kind"] == "syntax"
    assert res.error["line"] == 2 and res.error["column"] == 9  # expat points at the end-tag name
    assert slice_utf16(res.text, res.error["offset_utf16"], res.error["offset_utf16"] + 1) == "c"
    assert "well-formed" in res.error["message"] and "XML" in res.error["message"]
    assert res.text  # text still available for display


def test_malformed_unclosed_with_cyrillic_and_crlf():
    res = index_xml("<a>\r\n<б>привет</a>".encode())
    assert not res.ok
    assert res.error["line"] == 2
    assert slice_utf16(res.text, res.error["offset_utf16"], res.error["offset_utf16"] + 2) == "a>"


def test_malformed_empty_and_junk():
    assert not index_xml(b"").ok
    assert not index_xml(b"<a/><b/>").ok
    assert not index_xml(b"<a>&foo;</a>").ok


def test_bad_declared_encoding():
    res = index_xml(b'<?xml version="1.0" encoding="no-such-enc"?><a/>')
    assert not res.ok and res.error["kind"] == "encoding"


def test_undecodable_bytes():
    res = index_xml(b'<?xml version="1.0" encoding="UTF-8"?>\n<a>\xff\xfe</a>')
    assert not res.ok and res.error["kind"] == "encoding" and res.error["line"] == 2


def test_doctype_rejected():
    xml = b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY a "x">]>\n<r>&a;</r>'
    res = index_xml(xml)
    assert not res.ok and res.error["kind"] == "doctype"
    assert res.error["line"] == 2


def test_billion_laughs_rejected():
    xml = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
        "<lolz>&lol3;</lolz>"
    )
    res = index_xml(xml.encode())
    assert not res.ok and res.error["kind"] == "doctype"


def test_external_entity_rejected():
    xml = b'<?xml version="1.0"?><!DOCTYPE r SYSTEM "file:///etc/passwd"><r/>'
    assert index_xml(xml).error["kind"] == "doctype"
    xml = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    assert index_xml(xml).error["kind"] == "doctype"


def test_depth_limit():
    xml = b"<a>" * 11 + b"</a>" * 11
    assert index_xml(xml, max_depth=11).ok
    res = index_xml(xml, max_depth=10)
    assert not res.ok and res.error["kind"] == "limit" and "глубин" in res.error["message"]


def test_node_limit():
    xml = b"<r>" + b"<i/>" * 10 + b"</r>"
    assert index_xml(xml, max_nodes=11).ok
    res = index_xml(xml, max_nodes=10)
    assert not res.ok and res.error["kind"] == "limit"


def test_size_limit():
    res = index_xml(b"<a>" + b"x" * 100 + b"</a>", max_bytes=50)
    assert not res.ok and res.error["kind"] == "limit"


# ---- helpers --------------------------------------------------------------


def test_line_col_helpers():
    text = "ab\r\ncd\ref\n😀x"
    assert line_col_from_offset(text, 0) == (1, 1)
    assert line_col_from_offset(text, 2) == (1, 3)
    assert line_col_from_offset(text, 3) == (1, 4)  # inside CRLF stays on line 1
    assert line_col_from_offset(text, 4) == (2, 1)
    assert line_col_from_offset(text, 7) == (3, 1)
    assert line_col_from_offset(text, 10) == (4, 1)
    assert line_col_from_offset(text, 12) == (4, 3)
    assert utf16_len(text) == 13
    for off in (0, 1, 2, 4, 5, 7, 8, 10, 12, 13):
        assert offset_from_line_col(text, *line_col_from_offset(text, off)) == off
    with pytest.raises(ValueError):
        offset_from_line_col(text, 9, 1)
    with pytest.raises(ValueError):
        offset_from_line_col(text, 1, 99)
    assert slice_utf16(text, 10, 12) == "😀"


# ---- performance ------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("XMLDT_SLOW") != "1", reason="set XMLDT_SLOW=1 to run")
def test_large_file_performance():
    item = (
        "<cacdo:GDGoodsItemDetails><csdo:A>1</csdo:A><csdo:B code=\"x\">Товар номер 123</csdo:B>"
        "<cacdo:G><csdo:C>0808108000</csdo:C><csdo:D/></cacdo:G><csdo:E>abcdefghij</csdo:E>"
        "<csdo:F>zzzzzzzz</csdo:F><csdo:H>1.0</csdo:H><csdo:I>2</csdo:I></cacdo:GDGoodsItemDetails>\n"
    )
    head = f'<R xmlns="{NS_ROOT}" xmlns:csdo="{NS_CSDO}" xmlns:cacdo="{NS_CACDO}">\n'
    n = 50_000_000 // len(item.encode())
    data = (head + item * n + "</R>").encode()
    assert len(data) >= 49_000_000
    t0 = time.perf_counter()
    res = index_xml(data)
    elapsed = time.perf_counter() - t0
    print(f"indexed {len(res.nodes)} nodes, {len(data) / 1e6:.0f} MB in {elapsed:.1f}s")
    assert res.ok and len(res.nodes) >= 1_000_000
    assert elapsed < 30
    last = res.nodes[-1]
    assert slice_utf16(res.text, last.start, last.end).startswith("<csdo:")
