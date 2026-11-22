"""Trace rows identified by a key on a DuckDB SELECT's leftmost table."""

import math
import re
from datetime import date, time

from .query import Query, parse_query

_ID = r'(?:"(?:[^"]|"")+"|[^\W\d][\w$]*)'
_KEY = re.compile(rf"(?:(?P<qual>{_ID})\s*\.\s*)?(?P<col>{_ID})")
_TRUTH = (("TRUE", "TRUE"), ("FALSE", "FALSE"), ("UNKNOWN", "NULL"))
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


def _scan(plan: Query, key: str) -> tuple[str, int]:
    """Build a single read statement for counts and evidence from one snapshot."""
    rows = _cte_name("rows", plan)
    ctes, stats = [], []
    key_filter = f"WHERE {key} IS NOT DISTINCT FROM cast_to_type(?, {key})"
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
    if plan.where_sql:
        inputs = [f"input{i}" for i in range(len(plan.where_columns))]
        select = ", ".join([f"CAST(({plan.where_sql}\n) AS BOOLEAN)", *plan.where_columns])
        fields = f"({', '.join(['outcome', *inputs])})"
        stats.extend(f"count(*) FILTER (WHERE outcome IS {test})" for _, test in _TRUTH)
        if inputs:
            order = ", ".join(f"{col} ASC NULLS FIRST" for col in inputs)
            stats.extend(
                f"(SELECT list(sample ORDER BY {order}) FROM (SELECT {', '.join(inputs)} FROM {rows}"
                f" WHERE outcome IS {test} ORDER BY {order} LIMIT {_SAMPLE_LIMIT}) AS sample)"
                for _, test in _TRUTH
            )
    join = plan.join_sql or ""
    ctes.append(
        f"{rows}{fields} AS (SELECT {select} FROM {plan.base_sql} {join}\n{key_filter})"
    )
    return f"WITH {', '.join(ctes)} SELECT {', '.join(stats)} FROM {rows}", args


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
    kept, false, unknown, *samples = stats
    return {
        "stage": "where",
        "clause": f"WHERE {plan.where_sql}",
        "before": joined,
        "after": kept,
        "detail": (
            f"WHERE kept {kept} of {joined} row{'s' if joined != 1 else ''}. "
            f"{false} evaluated to FALSE and {unknown} to UNKNOWN (SQL NULL)."
        ),
        "truth_counts": {"TRUE": kept, "FALSE": false, "UNKNOWN": unknown},
        "samples": [
            {"outcome": outcome, "inputs": {col: _json(row[f"input{i}"]) for i, col in enumerate(plan.where_columns)}}
            for (outcome, _), rows in zip(_TRUTH, samples)
            for row in rows or []
        ],
    }


def trace_row(connection, sql: str, key: str, value) -> dict:
    """Return source, join, and filter evidence for all rows with the source key."""
    plan = parse_query(sql)
    key_col = _key_column(plan, key)
    connection.execute(f"EXPLAIN {plan.sql}")
    statement, args = _scan(plan, key_col)
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
        stages.append(_where_stage(plan, joined, where))
    output = stages[-1]["after"]
    return {
        "status": "present" if output else "removed" if source else "source_absent",
        "target": {"key": key, "value": _json(value)},
        "stages": stages,
        "output_rows": output,
    }
