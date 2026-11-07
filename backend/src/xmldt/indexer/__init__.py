"""XML node indexer with exact UTF-16 source coordinates."""

from .core import DEFAULT_GOODS_ITEM_NAMES, index_xml
from .models import IndexError_, IndexResult, Node
from .positions import line_col_from_offset, offset_from_line_col, slice_utf16, utf16_len

__all__ = [
    "DEFAULT_GOODS_ITEM_NAMES",
    "IndexError_",
    "IndexResult",
    "Node",
    "index_xml",
    "line_col_from_offset",
    "offset_from_line_col",
    "slice_utf16",
    "utf16_len",
]
