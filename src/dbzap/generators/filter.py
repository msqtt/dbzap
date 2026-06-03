"""LHS Brackets filter parser, search query, and SQLAlchemy query builder."""
from __future__ import annotations

import base64
import re
from typing import Any, cast

from sqlalchemy import ColumnElement, Table, and_, or_

_LHS_RE = re.compile(r"^([a-zA-Z_]\w*)\[([a-zA-Z_]+)\]$")

_VALID_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "like", "in", "is"})

_RESERVED_PARAMS = frozenset({
    "page", "page_size", "limit", "starting_after", "ending_before", "q",
})


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def encode_cursor(value: Any) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode()


def decode_cursor(token: str) -> str:
    try:
        return base64.urlsafe_b64decode(token.encode()).decode()
    except Exception as exc:
        raise ValueError(f"Invalid cursor: {token!r}") from exc


# ---------------------------------------------------------------------------
# Parse query params into filter conditions
# ---------------------------------------------------------------------------


def parse_filters(
    query_params: list[tuple[str, str]] | dict[str, str],
    valid_columns: set[str],
) -> list[dict[str, Any]]:
    """Extract filter conditions from URL query parameters.

    Returns a list of ``{field, op, value}`` dicts.  *value* may be a list
    when the same ``field[op]`` appears multiple times (treated as OR).

    Raises ``ValueError`` for unsupported operators.
    """
    items = list(query_params.items()) if isinstance(query_params, dict) else list(query_params)

    conditions: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}

    for key, value in items:
        if key in _RESERVED_PARAMS:
            continue

        m = _LHS_RE.match(key)
        if m:
            field, op = m.group(1), m.group(2)
            if op not in _VALID_OPS:
                raise ValueError(f"Unsupported filter operator: {op!r}")
            if field not in valid_columns:
                continue
            dedup_key = (field, op)
            if dedup_key in seen:
                existing = seen[dedup_key]["value"]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    seen[dedup_key]["value"] = [existing, value]
            else:
                cond: dict[str, Any] = {"field": field, "op": op, "value": value}
                seen[dedup_key] = cond
                conditions.append(cond)
        else:
            if key.startswith("_"):
                continue
            if key not in valid_columns:
                continue
            dedup_key = (key, "eq")
            if dedup_key in seen:
                existing = seen[dedup_key]["value"]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    seen[dedup_key]["value"] = [existing, value]
            else:
                cond = {"field": key, "op": "eq", "value": value}
                seen[dedup_key] = cond
                conditions.append(cond)

    return conditions


# ---------------------------------------------------------------------------
# Build SQLAlchemy WHERE clause
# ---------------------------------------------------------------------------


def _build_condition(
    cond: dict[str, Any], sa_tbl: Table,
) -> ColumnElement[bool] | None:
    field = cond["field"]
    if field not in sa_tbl.c:
        return None
    col = sa_tbl.c[field]
    op = cond["op"]
    raw = cond["value"]

    if isinstance(raw, list):
        sub_parts = [_build_single_condition(col, op, v) for v in raw]
        nonnull_parts: list[ColumnElement[bool]] = [p for p in sub_parts if p is not None]
        if not nonnull_parts:
            return None
        if len(nonnull_parts) == 1:
            return nonnull_parts[0]
        return or_(*nonnull_parts)

    return _build_single_condition(col, op, raw)


def _build_single_condition(
    col: Any, op: str, raw: str,
) -> ColumnElement[bool] | None:
    if op == "eq":
        return cast(ColumnElement[bool], col == raw)
    if op == "ne":
        return cast(ColumnElement[bool], col != raw)
    if op == "gt":
        return cast(ColumnElement[bool], col > raw)
    if op == "gte":
        return cast(ColumnElement[bool], col >= raw)
    if op == "lt":
        return cast(ColumnElement[bool], col < raw)
    if op == "lte":
        return cast(ColumnElement[bool], col <= raw)
    if op == "like":
        return cast(ColumnElement[bool], col.like(f"%{raw}%"))
    if op == "in":
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        return cast(ColumnElement[bool], col.in_(vals))
    if op == "is":
        if raw == "null":
            return cast(ColumnElement[bool], col.is_(None))
        return cast(ColumnElement[bool], col.isnot(None))
    return None


def apply_filters(
    query: Any,
    sa_tbl: Table,
    conditions: list[dict[str, Any]],
) -> Any:
    """Apply parsed filter conditions to a SQLAlchemy select query.

    All conditions are ANDed together.
    """
    if not conditions:
        return query

    parts: list[ColumnElement[bool]] = []
    for cond in conditions:
        clause = _build_condition(cond, sa_tbl)
        if clause is not None:
            parts.append(clause)

    if parts:
        query = query.where(and_(*parts))
    return query


# ---------------------------------------------------------------------------
# Global search (q parameter)
# ---------------------------------------------------------------------------


def apply_search(
    query: Any,
    sa_tbl: Table,
    q: str,
    string_columns: set[str],
) -> Any:
    """Apply global text search across all string columns.

    Generates ``WHERE (col1 LIKE '%q%' OR col2 LIKE '%q%' OR ...)``
    and ANDs it with any existing WHERE clause.

    If *string_columns* is empty or *q* is empty, the query is returned
    unchanged.
    """
    if not q or not string_columns:
        return query

    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    or_parts: list[ColumnElement[bool]] = []
    for col_name in sorted(string_columns):
        if col_name in sa_tbl.c:
            or_parts.append(sa_tbl.c[col_name].like(pattern))

    if or_parts:
        query = query.where(or_(*or_parts))
    return query
