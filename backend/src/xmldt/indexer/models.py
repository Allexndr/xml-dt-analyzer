"""Data models produced by the XML indexer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class IndexError_(TypedDict):
    """Structured error. ``kind``: syntax | doctype | limit | encoding."""

    message: str
    line: int
    column: int
    offset_utf16: int
    kind: str


@dataclass(slots=True)
class Node:
    """One element. Offsets are UTF-16 code units into ``IndexResult.text``."""

    node_id: int
    parent_id: int | None
    depth: int
    ns: str
    local_name: str
    prefix: str
    spath: str  # schema path without positions, interned (shared by all same-path nodes)
    ordinal: int  # 1-based position among same-name siblings
    parent: "Node | None"
    attrs: dict[str, str]
    value: str | None  # stripped text for leaf elements ('' if empty), None otherwise
    child_count: int
    goods_no: int | None
    start: int = -1
    end: int = -1
    value_start: int | None = None
    value_end: int | None = None
    line_start: int = 0
    line_end: int = 0

    @property
    def path(self) -> str:
        """Full positional path, built on demand (not stored, to keep the index small)."""
        parts = []
        n: Node | None = self
        while n is not None:
            parts.append(f"{n.local_name}[{n.ordinal}]")
            n = n.parent
        return "/" + "/".join(reversed(parts))

    @property
    def qname(self) -> str:
        """Name as written in the source (``prefix:local`` or ``local``)."""
        return f"{self.prefix}:{self.local_name}" if self.prefix else self.local_name


@dataclass(slots=True)
class IndexResult:
    """Result of indexing; ``nodes[i].node_id == i``."""

    ok: bool
    text: str = ""
    encoding: str = ""
    has_bom: bool = False
    nodes: list[Node] = field(default_factory=list)
    error: IndexError_ | None = None
