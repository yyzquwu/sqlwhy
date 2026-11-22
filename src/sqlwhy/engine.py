"""Trace rows identified by a key on a DuckDB SELECT's leftmost table."""

import math
import re
from datetime import date, time

import duckdb

from .query import Query, needs_native_guard, parse_query

_ID = r'(?:"(?:[^"]|"")+"|[^\W\d][\w$]*)'
_KEY = re.compile(rf"(?:(?P<qual>{_ID})\s*\.\s*)?(?P<col>{_ID})")
_TRUTH = ("TRUE", "FALSE", "UNKNOWN")
_SAMPLE_LIMIT = 3
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _unquote(ident: str) -> str:
    return ident[1:-1].replace('""', '"') if ident.startswith('"') else ident


def _key_column(plan: Query, key: str) -> str:
    match = _KEY.fullmatch(key.strip())
    if not match:
        raise ValueError("Use a source column name or a qualified source column as the key.")
    qualifier, col = match.groups()
    if qualifier:
        name = _unquote(qualifier).translate(_ASCII_LOWER)
        alias = plan.base_alias.translate(_ASCII_LOWER)
        related = {ident.translate(_ASCII_LOWER) for ident in (plan.right_alias, plan.right_name) if ident}
        if name != alias and (name != plan.base_name.translate(_ASCII_LOWER) or name in related):
            raise ValueError(f"The key must refer to the source table ({plan.base_alias}).")
    return f"{_quote(plan.base_alias)}.{_quote(_unquote(col))}"


def _cte_name(label: str, plan: Query) -> str:
    name = f"__sqlwhy_{label}"
    relations = {ident.lower() for ident in (plan.base_name, plan.right_name) if ident}
    while name in relations:
        name += "_"
    return name


def _sample_columns(connection, plan):
    if not plan.where_columns:
        return [], {}
    names = ", ".join(plan.where_columns)
    info = connection.execute(f"SELECT {names} FROM {plan.base_sql} {plan.join_sql or ''} LIMIT 0").description
    types = {col: row[1] for col, row in zip(plan.where_columns, info, strict=True)}
    bound_types = {col: str(kind) for col, kind in types.items()}
    # DuckDB's Python client discards UNION tags even when the tag determines
    # equality. Keep the counts but omit examples whose inputs would be lossy.
    if any(re.search(r"\bUNION\(", kind) for kind in bound_types.values()):
        return [], bound_types
    samples = []
    for col, kind in types.items():
        projected = _sample_type(kind)
        if kind.id in ("time_ns", "timestamp_ns"):
            samples.append(f"replace(CAST({col} AS VARCHAR), ' ', 'T')")
        elif projected != kind:
            samples.append(f"CAST({col} AS {projected})")
        else:
            samples.append(col)
    return samples, bound_types


def _sample_type(kind):
    """Change only nanosecond leaves to text before DuckDB converts them to Python."""
    if kind.id in ("timestamp_ns", "time_ns"):
        return duckdb.sqltypes.VARCHAR
    if kind.id == "list":
        return duckdb.list_type(_sample_type(kind.children[0][1]))
    if kind.id == "array":
        child, size = kind.children
        return duckdb.array_type(_sample_type(child[1]), size[1])
    if kind.id == "map":
        return duckdb.map_type(*(_sample_type(t) for _, t in kind.children))
    if kind.id == "struct":
        return duckdb.struct_type({k: _sample_type(t) for k, t in kind.children})
    return kind


def _scan(plan: Query, key: str, columns: list[str], native_only=False, null_key=False, text_inputs=()) -> tuple[str, int]:
    """Build a single read statement for counts and evidence from one snapshot."""
    rows = _cte_name("rows", plan)
    ctes, stats = [], []
    key_filter = f"WHERE {key} IS NULL" if null_key else f"WHERE {key} IS NOT DISTINCT FROM cast_to_type(?, {key})"
    args = 1
    if plan.join_sql:
        source = _cte_name("source", plan)
        unmatched = _cte_name("unmatched", plan)
        ctes.extend([
            f"{source} AS (SELECT 1 FROM {plan.base_sql} {key_filter})",
            f"{unmatched} AS (SELECT 1 FROM {plan.base_sql} ANTI JOIN {plan.right_sql}"
            f" ON {plan.on_sql}\n{key_filter})",
        ])
        stats.extend([f"(SELECT count(*) FROM {source})", f"(SELECT count(*) FROM {unmatched})"])
        args += 2

    stats.append("count(*)")
    select, fields = "1", ""
    join = plan.join_sql or ""
    if plan.where_sql and native_only:
        stats.append(f"(SELECT count(*) FROM {plan.base_sql} {join}\n{key_filter} AND ({plan.where_sql}\n))")
        args += 1
    elif plan.where_sql:
        inputs = [f"input{i}" for i in range(len(columns))]
        outcome = f"CAST(({plan.where_sql}\n) AS BOOLEAN)"
        select = ", ".join([outcome, *columns])
        fields = f"({', '.join(['outcome', *inputs])})"
        tests = ("TRUE", "FALSE", "NULL")
        stats.extend(f"count(*) FILTER (WHERE outcome IS {test})" for test in tests)
        if inputs:
            # Preserve SQL's collation order, then resolve ties by the original UTF-8 bytes.
            terms = [f"{col} ASC NULLS FIRST" for col in inputs]
            terms.extend(f"encode(input{i}) ASC NULLS FIRST" for i in text_inputs)
            order = ", ".join(terms)
            stats.extend(
                f"(SELECT list(sample ORDER BY {order}) FROM (SELECT {', '.join(inputs)} FROM {rows}"
                f" WHERE outcome IS {test} ORDER BY {order} LIMIT {_SAMPLE_LIMIT}) AS sample)"
                for test in tests
            )
    ctes.append(
        f"{rows}{fields} AS (SELECT {select} FROM {plan.base_sql} {join}\n{key_filter})"
    )
    return f"WITH {', '.join(ctes)} SELECT {', '.join(stats)} FROM {rows}", 0 if null_key else args


def _json(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return str(value)


def _join_stage(plan: Query, source: int, unmatched: int, joined: int) -> dict:
    left = plan.join_kind == "left"
    preserved = unmatched if left else 0
    removed = unmatched - preserved
    pairs = joined - preserved
    action = "preserved" if left else "removed"
    suffix = " with NULLs on the right" if left else ""
    detail = (
        f"The join produced {pairs} matched pair{'s' if pairs != 1 else ''} "
        f"and {action} {unmatched} unmatched source row{'s' if unmatched != 1 else ''}{suffix}."
    )
    return {
        "stage": "join",
        "clause": plan.join_sql,
        "before": source,
        "after": joined,
        "detail": detail,
        "join_kind": plan.join_kind,
        "matched_source_rows": source - unmatched,
        "matched_pairs": pairs,
        "unmatched_source_rows": unmatched,
        "preserved_unmatched_rows": preserved,
        "removed_source_rows": removed,
    }


def _where_stage(plan: Query, joined: int, stats: list) -> dict:
    if len(stats) == 1:
        kept = stats[0]
        return {
            "stage": "where", "clause": f"WHERE {plan.where_sql}", "before": joined, "after": kept,
            "detail": (f"DuckDB's WHERE filter kept {kept} of {joined} rows. The compound predicate "
                       "may guard an operation, so rejected rows are unclassified."),
            "truth_counts": {"TRUE": kept, "FALSE": None, "UNKNOWN": None, "UNCLASSIFIED": joined - kept},
            "samples": [],
        }
    kept, false, unknown, *samples = stats
    counts = {"TRUE": kept, "FALSE": false, "UNKNOWN": unknown}
    detail = (f"WHERE kept {kept} of {joined} row{'s' if joined != 1 else ''}. "
              f"{false} evaluated to FALSE and {unknown} to UNKNOWN (SQL NULL).")
    return {
        "stage": "where",
        "clause": f"WHERE {plan.where_sql}",
        "before": joined,
        "after": kept,
        "detail": detail,
        "truth_counts": counts,
        "samples": [
            {"outcome": outcome, "inputs": {
                col: _json(row[f"input{i}"])
                for i, col in enumerate(plan.where_columns)}}
            for outcome, rows in zip(_TRUTH, samples)
            for row in rows or []
        ],
    }


def _prepare_trace(connection, plan, key_col):
    columns, types = _sample_columns(connection, plan)
    text_inputs = tuple(i for i, kind in enumerate(types.values()) if kind == "VARCHAR")
    return columns, needs_native_guard(plan.where_expr, types), text_inputs


def _trace(connection, plan, key, key_col, value, prepared):
    columns, native_only, text_inputs = prepared
    statement, args = _scan(plan, key_col, columns, native_only=native_only, null_key=value is None,
                            text_inputs=text_inputs)
    stats = connection.execute(statement, [value] * args).fetchone()
    source, unmatched, joined, *where = stats if plan.join_sql else (stats[0], 0, *stats)
    stages = [{
        "stage": "source",
        "clause": f"FROM {plan.base_sql}",
        "before": None,
        "after": source,
        "detail": f"Found {source} matching source row{'s' if source != 1 else ''}.",
    }]
    if plan.join_sql:
        stages.append(_join_stage(plan, source, unmatched, joined))
    if plan.where_sql:
        stage = _where_stage(plan, joined, where)
        if plan.where_columns and not columns:
            stage["detail"] += " Samples omitted because a predicate input contains a tagged UNION."
        stages.append(stage)
    output = stages[-1]["after"]
    return {
        "status": "present" if output else "removed" if source else "source_absent",
        "target": {"key": key, "value": _json(value)},
        "stages": stages,
        "output_rows": output,
    }


def trace_row(connection, sql: str, key: str, value) -> dict:
    """Return source, join, and filter evidence for all rows with the source key."""
    plan = parse_query(sql)
    key_col = _key_column(plan, key)
    connection.execute(f"EXPLAIN {plan.sql}")
    return _trace(connection, plan, key, key_col, value, _prepare_trace(connection, plan, key_col))
