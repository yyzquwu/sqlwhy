"""Compare the multiplicity of each key in two saved DuckDB relations."""
import re

from .audit import _BIGINT_MAX, _KEY_TYPES
from .engine import _ID, _json, _quote, _unquote
from .query import _FOLD


def _relation(name):
    if not isinstance(name, str) or not re.fullmatch(rf"{_ID}(?:\s*\.\s*{_ID}){{0,2}}", name.strip()):
        raise ValueError("Use a table or view name, optionally qualified by schema or catalog.")
    return ".".join(_quote(_unquote(part)) for part in re.findall(_ID, name.strip()))


def _input(con, name, key):
    table = _relation(name)
    columns = con.execute(f"SELECT * FROM {table} LIMIT 0").description
    if not isinstance(key, str) or not key:
        raise ValueError("Use a key column name.")
    for col, kind, *_ in columns:
        if col.translate(_FOLD) == key.translate(_FOLD):
            if kind.id not in _KEY_TYPES:
                raise ValueError(f"Unsupported diff key type {kind} in {name}.")
            ident = _quote(col)
            # Use a common binary order independent of either relation's text collation.
            return table, (f"encode({ident})" if kind.id == "varchar" else ident), kind
    raise ValueError(f"Unknown key {key!r} in {name}.")


def diff_rows(connection, before: str, after: str, key: str, *, after_key=None, limit=5) -> dict:
    """Compare row counts per key. Runs against tables or views on the supplied connection."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer.")
    left, lk, lt = _input(connection, before, key)
    right, rk, rt = _input(connection, after, key if after_key is None else after_key)
    if lt != rt or str(lt) != str(rt):
        raise ValueError(f"Diff key types must match: {lt} in {before}, {rt} in {after}.")
    counts = "__sqlwhy_diff"
    # Avoid shadowing a supplied table when it appears inside the generated CTE.
    while _quote(counts) in (left.lower(), right.lower()):
        counts += "_"
    display = "decode(k)" if lt.id == "varchar" else "k"
    result = connection.execute(f"""
        WITH {counts} AS (
            SELECT k, count(*) FILTER (WHERE old) AS b, count(*) FILTER (WHERE NOT old) AS a
            FROM (SELECT {lk} AS k, true AS old FROM {left}
                  UNION ALL SELECT {rk}, false FROM {right}) AS inputs
            GROUP BY k
        )
        SELECT coalesce(sum(b), 0), coalesce(sum(a), 0),
               count(*) FILTER (WHERE b = 0), count(*) FILTER (WHERE a = 0),
               count(*) FILTER (WHERE b > 0 AND a > b), count(*) FILTER (WHERE a > 0 AND a < b),
               ARRAY(SELECT struct_pack(key := {display}, before_rows := b, after_rows := a)
                     FROM {counts} WHERE b <> a ORDER BY k NULLS FIRST LIMIT ?)
        FROM {counts}
    """, [min(limit, _BIGINT_MAX)]).fetchone()
    b, a, added, removed, increased, decreased, examples = result
    return {
        "before_rows": b, "after_rows": a, "added_keys": added, "removed_keys": removed,
        "increased_keys": increased, "decreased_keys": decreased,
        "changed_keys": added + removed + increased + decreased, "examples": _json(examples),
    }
