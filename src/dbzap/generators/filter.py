"""LHS Brackets filter parser and SQLAlchemy query builder."""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from sqlalchemy import ColumnElement, Table, and_, or_

_LHS_RE = re.compile(r"^([a-zA-Z_]\w*)\[([a-zA-Z_]+)\]$")

_VALID_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "like", "in", "is"})

_RESERVED_PARAMS = frozenset({
    "page", "page_size", "limit", "starting_after", "ending_before",
    "_or", "_filter",
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
) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract filter conditions from URL query parameters.

    Accepts either a list of ``(key, value)`` tuples (preserving duplicates)
    or a plain dict.  Returns ``(conditions, or_fields)`` where each condition
    is a dict ``{field, op, value}``.  *value* may be a list when the same
    ``field[op]`` appears multiple times — the query builder treats those as OR.

    Raises ``ValueError`` for unsupported operators or malformed ``_filter``
    JSON.
    """
    if isinstance(query_params, dict):
        items = list(query_params.items())
    else:
        items = list(query_params)

    conditions: list[dict[str, Any]] = []
    or_fields: list[str] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}

    for key, value in items:
        if key in _RESERVED_PARAMS:
            if key == "_or":
                or_fields = [f.strip() for f in value.split(",") if f.strip()]
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

    filter_param = None
    for key, value in items:
        if key == "_filter":
            filter_param = value
            break
    if filter_param:
        try:
            expr = json.loads(filter_param)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed _filter JSON: {exc}") from exc
        conditions.extend(_flatten_json_filter(expr, valid_columns))

    return conditions, or_fields


def _flatten_json_filter(
    expr: Any, valid_columns: set[str],
) -> list[dict[str, Any]]:
    """Recursively flatten a JSON filter expression into condition dicts.

    Logical groups (``or`` / ``and``) are tagged with ``_logic`` so the
    query builder can reconstruct the tree.
    """
    if not isinstance(expr, dict):
        raise ValueError("_filter must be a JSON object")

    if "or" in expr:
        groups = []
        for sub in expr["or"]:
            groups.append(_flatten_json_filter(sub, valid_columns))
        return [{"_logic": "or", "groups": groups}]

    if "and" in expr:
        groups = []
        for sub in expr["and"]:
            groups.append(_flatten_json_filter(sub, valid_columns))
        return [{"_logic": "and", "groups": groups}]

    field = expr.get("field")
    op = expr.get("op", "eq")
    value = expr.get("value")
    if not field or op not in _VALID_OPS:
        raise ValueError(f"Invalid filter condition: {expr}")
    if field not in valid_columns:
        return []
    return [{"field": field, "op": op, "value": str(value) if value is not None else ""}]


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
        parts = [_build_single_condition(col, op, v) for v in raw]
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return or_(*parts)

    return _build_single_condition(col, op, raw)


def _build_single_condition(
    col: Any, op: str, raw: str,
) -> ColumnElement[bool] | None:
    if op == "eq":
        return col == raw
    if op == "ne":
        return col != raw
    if op == "gt":
        return col > raw
    if op == "gte":
        return col >= raw
    if op == "lt":
        return col < raw
    if op == "lte":
        return col <= raw
    if op == "like":
        return col.like(f"%{raw}%")
    if op == "in":
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        return col.in_(vals)
    if op == "is":
        if raw == "null":
            return col.is_(None)
        return col.isnot(None)
    return None


def _build_group(
    conds: list[dict[str, Any]], sa_tbl: Table,
) -> ColumnElement[bool] | None:
    """Build a WHERE clause from a list of conditions (may include _logic groups)."""
    parts: list[ColumnElement[bool]] = []
    for cond in conds:
        if "_logic" in cond:
            sub_parts: list[ColumnElement[bool]] = []
            for group in cond["groups"]:
                sub = _build_group(group, sa_tbl)
                if sub is not None:
                    sub_parts.append(sub)
            if not sub_parts:
                continue
            if cond["_logic"] == "or":
                parts.append(or_(*sub_parts))
            else:
                parts.append(and_(*sub_parts))
        else:
            clause = _build_condition(cond, sa_tbl)
            if clause is not None:
                parts.append(clause)
    if not parts:
        return None
    return and_(*parts)


def apply_filters(
    query: Any,
    sa_tbl: Table,
    conditions: list[dict[str, Any]],
    or_fields: list[str],
) -> Any:
    """Apply parsed filter conditions to a SQLAlchemy select query.

    Top-level conditions are ANDed.  Conditions whose *field* appears in
    *or_fields* are grouped into a single OR clause that is itself ANDed
    with the remaining conditions.
    """
    if not conditions:
        return query

    or_set = set(or_fields)

    or_conds = [c for c in conditions if c.get("field") in or_set and "_logic" not in c]
    and_conds = [c for c in conditions if c.get("field") not in or_set or "_logic" in c]

    and_parts: list[ColumnElement[bool]] = []

    for cond in and_conds:
        if "_logic" in cond:
            clause = _build_group([cond], sa_tbl)
        else:
            clause = _build_condition(cond, sa_tbl)
        if clause is not None:
            and_parts.append(clause)

    if or_conds:
        or_parts: list[ColumnElement[bool]] = []
        for cond in or_conds:
            clause = _build_condition(cond, sa_tbl)
            if clause is not None:
                or_parts.append(clause)
        if or_parts:
            and_parts.append(or_(*or_parts))

    if and_parts:
        query = query.where(and_(*and_parts))
    return query
