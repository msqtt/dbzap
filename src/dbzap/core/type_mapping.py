import datetime
import decimal
import re
import uuid
from typing import Any, cast

_MAPPING: dict[str, type[Any]] = {
    "INTEGER": int,
    "INT": int,
    "SMALLINT": int,
    "SERIAL": int,
    "BIGINT": int,
    "BIGSERIAL": int,
    "FLOAT": float,
    "REAL": float,
    "DOUBLE PRECISION": float,
    "NUMERIC": decimal.Decimal,
    "DECIMAL": decimal.Decimal,
    "BOOLEAN": bool,
    "VARCHAR": str,
    "CHAR": str,
    "TEXT": str,
    "CLOB": str,
    "DATE": datetime.date,
    "TIME": datetime.time,
    "TIME WITHOUT TIME ZONE": datetime.time,
    "TIMESTAMP": datetime.datetime,
    "TIMESTAMP WITHOUT TIME ZONE": datetime.datetime,
    "TIMESTAMP WITH TIME ZONE": datetime.datetime,
    "TIMESTAMPTZ": datetime.datetime,
    "BYTEA": bytes,
    "BLOB": bytes,
    "UUID": uuid.UUID,
    "JSON": dict,
    "JSONB": dict,
}

_STRIP_PARENS = re.compile(r"\s*\(.*\)\s*$")


def map_sql_type_to_python(sql_type: str) -> type[Any]:
    normalized = sql_type.upper().strip()

    if normalized.endswith("[]"):
        return list

    if normalized.startswith("ARRAY"):
        return list

    normalized = _STRIP_PARENS.sub("", normalized).strip()

    result = _MAPPING.get(normalized)
    if result is not None:
        return result

    return cast(type[Any], Any)
