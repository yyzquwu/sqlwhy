"""Audit the report row count for every distinct left-source key in DuckDB."""

from .engine import _cte_name, _key_column, _prepare_trace, _trace
from .query import _FOLD, parse_query, projected_key

_KEY_TYPES = {
    "boolean", "tinyint", "smallint", "integer", "bigint", "hugeint",
    "utinyint", "usmallint", "uinteger", "ubigint", "uhugeint", "decimal",
    "float", "double", "varchar", "blob", "uuid",
}
_BIGINT_MAX = 2**63 - 1


def _columns(connection, table):
    if table is None:
        return ()
    return tuple(col[0] for col in connection.execute(f"SELECT * FROM {table} LIMIT 0").description)


def _audit_sql(plan, key_col, index, width, text_key):
    report = _cte_name("report", plan)
    counts = _cte_name("counts", plan)
    totals = _cte_name("totals", plan)
    columns = ", ".join(f"c{i}" for i in range(width))
    sample = "decode(min(encode(k)) FILTER (WHERE is_source))" if text_key else "k"
    # The column counts force DuckDB to evaluate the original projection, including fallible casts.
    return f"""
        WITH {report}({columns}) AS MATERIALIZED (
            {plan.statement_sql}
        ), {counts} AS (
            SELECT k, {sample} AS sample_value,
                   count(*) FILTER (WHERE is_source) AS source_rows,
                   count(*) FILTER (WHERE NOT is_source) AS output_rows
            FROM (
                SELECT {key_col} AS k, TRUE AS is_source FROM {plan.base_sql}
                UNION ALL
                SELECT c{index}, FALSE FROM {report}
            ) AS all_rows
            GROUP BY k
            HAVING count(*) FILTER (WHERE is_source) > 0
        ), {totals} AS (
            SELECT count(*) AS report_rows, count(COLUMNS(*)) FROM {report}
        )
        SELECT source_rows, source_keys, violation_count, matched_report_rows,
               ARRAY(SELECT struct_pack(value := sample_value, source_rows := source_rows, output_rows := output_rows)
                     FROM {counts} WHERE output_rows <> ? ORDER BY k ASC NULLS FIRST LIMIT ?),
               {totals}.*
        FROM (
            SELECT coalesce(sum(source_rows), 0) AS source_rows, count(*) AS source_keys,
                   count(*) FILTER (WHERE output_rows <> ?) AS violation_count,
                   coalesce(sum(output_rows), 0) AS matched_report_rows
            FROM {counts}
        ) AS summary CROSS JOIN {totals}
    """


def audit_rows(connection, sql: str, key: str, expected_rows: int, limit: int = 5) -> dict:
    """Count all source-key violations and trace at most `limit` examples in key order.

    Use a DuckDB transaction if concurrent writes could change data between the
    bulk count and example traces. Keys must round-trip losslessly through Python.
    """
    for label, value in (("expected_rows", expected_rows), ("limit", limit)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must be a nonnegative integer.")

    plan = parse_query(sql)
    key_col = _key_column(plan, key)
    meta = connection.execute(f"SELECT {key_col} FROM {plan.base_sql} LIMIT 0").description[0]
    key_name, key_type = meta[:2]
    source_names = _columns(connection, plan.base_sql)
    if key_name.translate(_FOLD) not in {name.translate(_FOLD) for name in source_names}:
        raise ValueError("Audit requires a key column exposed by the source table.")
    if key_type.id not in _KEY_TYPES:
        raise ValueError(f"Audit does not support key type {key_type}.")

    report = connection.execute(f"DESCRIBE {plan.statement_sql}").fetchall()
    index = projected_key(plan, key_name, source_names,
                          _columns(connection, plan.right_sql), [col[0] for col in report])
    if report[index][1] != str(key_type):
        raise ValueError("Audit requires the projected source key to keep its original type.")
    statement = _audit_sql(plan, key_col, index, len(report), key_type.id == "varchar")
    expected = min(expected_rows, _BIGINT_MAX + 1)
    stats = connection.execute(statement, [expected, min(limit, _BIGINT_MAX), expected]).fetchone()
    source_rows, source_keys, violation_count, matched, samples, report_rows, *_ = stats
    if matched != report_rows:
        raise ValueError("Audit could not match all report rows to source keys.")

    examples = []
    if samples:
        prepared = _prepare_trace(connection, plan, key_col)
        for sample in samples:
            trace = _trace(connection, plan, key, key_col, sample["value"], prepared)
            if (trace["output_rows"], trace["stages"][0]["after"]) != (sample["output_rows"], sample["source_rows"]):
                raise ValueError("Audit example counts changed or could not be reproduced by the trace.")
            examples.append(trace)
    return {
        "key": key, "expected_rows": expected_rows, "source_rows": source_rows, "source_keys": source_keys,
        "report_rows": report_rows, "violation_count": violation_count, "examples": examples,
    }
