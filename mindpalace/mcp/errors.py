"""
Map domain exceptions to MCP errors with structured payloads.

Why not just let exceptions propagate: FastMCP would render them as plain
text. We want the LLM to see an error *code* it can branch on
(``PATH_NOT_FOUND``, ``ENTITY_LOCKED``, …) plus a payload it can use to
recover (``available_children``, …).

Usage:
    @mcp.tool
    def something(...) -> Out:
        try:
            ...
        except PathNotFoundError as e:
            raise mcp_error(e)
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastmcp.exceptions import ToolError

from mindpalace.core.operations import (
    EntityLockedError,
    InvalidMoveError,
    ItemConflictError,
    RootNotInitializedError,
    RootOperationError,
    SiblingConflictError,
)
from mindpalace.core.resolver import (
    ItemAmbiguousError,
    ItemNotFoundError,
    PathAmbiguousError,
    PathNotFoundError,
)


_CODE_BY_TYPE: dict[type, str] = {
    PathNotFoundError: "PATH_NOT_FOUND",
    PathAmbiguousError: "PATH_AMBIGUOUS",
    ItemNotFoundError: "ITEM_NOT_FOUND",
    ItemAmbiguousError: "ITEM_AMBIGUOUS",
    SiblingConflictError: "SIBLING_CONFLICT",
    ItemConflictError: "ITEM_CONFLICT",
    InvalidMoveError: "INVALID_MOVE",
    RootOperationError: "ROOT_OPERATION_FORBIDDEN",
    RootNotInitializedError: "ROOT_NOT_INITIALIZED",
    EntityLockedError: "ENTITY_LOCKED",
}


def _payload(exc: Exception) -> dict[str, Any]:
    if is_dataclass(exc):
        # The Container/Item refs in some exception fields can't be JSON-ised
        # cleanly; coerce by replacing with their .path attribute.
        raw = asdict(exc)
        return {k: _coerce(v) for k, v in raw.items()}
    return {"message": str(exc)}


def _coerce(v: Any) -> Any:
    if hasattr(v, "path"):
        return v.path
    if isinstance(v, list):
        return [_coerce(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce(val) for k, val in v.items()}
    return v


def mcp_error(exc: Exception) -> ToolError:
    """
    Wrap a domain exception in a FastMCP ``ToolError`` whose message starts
    with a stable error code (``PATH_NOT_FOUND``, ``ENTITY_LOCKED``, …) and
    is followed by a JSON payload the LLM can parse for recovery hints.

    FastMCP 3 ToolError doesn't (yet) carry a separate ``data`` field, so we
    pack everything into the message string. Format:

        ``CODE: human message :: <json payload>``
    """
    import json
    code = _CODE_BY_TYPE.get(type(exc), "INTERNAL_ERROR")
    payload = _payload(exc)
    return ToolError(
        f"{code}: {exc} :: {json.dumps({'code': code, **payload}, default=str)}"
    )
