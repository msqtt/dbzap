import datetime
import decimal
import uuid
from typing import Any

import pytest


class TestMapSqlTypeToPython:
    @pytest.mark.parametrize(
        "sql_type,expected",
        [
            ("INTEGER", int),
            ("INT", int),
            ("SMALLINT", int),
            ("SERIAL", int),
            ("integer", int),
            ("Int", int),
        ],
    )
    def test_integer_variants(self, sql_type: str, expected: type) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is expected

    @pytest.mark.parametrize(
        "sql_type,expected",
        [
            ("BIGINT", int),
            ("BIGSERIAL", int),
            ("bigint", int),
        ],
    )
    def test_bigint_variants(self, sql_type: str, expected: type) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is expected

    @pytest.mark.parametrize(
        "sql_type,expected",
        [
            ("FLOAT", float),
            ("REAL", float),
            ("DOUBLE PRECISION", float),
            ("double precision", float),
        ],
    )
    def test_float_variants(self, sql_type: str, expected: type) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is expected

    @pytest.mark.parametrize(
        "sql_type",
        ["NUMERIC", "DECIMAL", "NUMERIC(10,2)", "DECIMAL(5,0)", "numeric(10, 2)"],
    )
    def test_numeric_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is decimal.Decimal

    def test_boolean(self) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python("BOOLEAN") is bool
        assert map_sql_type_to_python("boolean") is bool

    @pytest.mark.parametrize(
        "sql_type",
        ["VARCHAR", "CHAR", "TEXT", "CLOB", "VARCHAR(255)", "VARCHAR(100)", "varchar(255)", "char(10)"],
    )
    def test_string_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is str

    def test_date(self) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python("DATE") is datetime.date
        assert map_sql_type_to_python("date") is datetime.date

    @pytest.mark.parametrize(
        "sql_type",
        ["TIME", "TIME WITHOUT TIME ZONE", "time without time zone"],
    )
    def test_time_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is datetime.time

    @pytest.mark.parametrize(
        "sql_type",
        ["TIMESTAMP", "TIMESTAMP WITHOUT TIME ZONE", "timestamp"],
    )
    def test_timestamp_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is datetime.datetime

    @pytest.mark.parametrize(
        "sql_type",
        ["TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ", "timestamptz", "timestamp with time zone"],
    )
    def test_timestamptz_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is datetime.datetime

    @pytest.mark.parametrize("sql_type", ["BYTEA", "BLOB", "bytea"])
    def test_binary_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is bytes

    def test_uuid(self) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python("UUID") is uuid.UUID
        assert map_sql_type_to_python("uuid") is uuid.UUID

    @pytest.mark.parametrize("sql_type", ["JSON", "JSONB", "json", "jsonb"])
    def test_json_variants(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is dict

    @pytest.mark.parametrize(
        "sql_type",
        ["INTEGER[]", "TEXT[]", "BIGINT[]", "integer[]"],
    )
    def test_array_suffix_notation(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is list

    @pytest.mark.parametrize(
        "sql_type",
        ["ARRAY(INTEGER)", "ARRAY(TEXT)", "ARRAY", "array(integer)"],
    )
    def test_array_prefix_notation(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python(sql_type) is list

    @pytest.mark.parametrize(
        "sql_type",
        ["UNKNOWNTYPE", "CUSTOMTYPE", "MY_ENUM", ""],
    )
    def test_unknown_returns_any(self, sql_type: str) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        result = map_sql_type_to_python(sql_type)
        assert result is Any

    def test_case_insensitive(self) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python("varchar") is str
        assert map_sql_type_to_python("VarChar") is str
        assert map_sql_type_to_python("VARCHAR") is str

    def test_idempotent(self) -> None:
        from dbzap.core.type_mapping import map_sql_type_to_python

        assert map_sql_type_to_python("INTEGER") is map_sql_type_to_python("INTEGER")
        assert map_sql_type_to_python("VARCHAR(255)") is map_sql_type_to_python("VARCHAR(255)")
