"""Compare the multiplicity of each key in two saved DuckDB relations."""
import re

from .audit import _BIGINT_MAX, _KEY_TYPES
from .engine import _ID, _json, _quote, _unquote
from .query import _FOLD


def _relation(name):
    if not isinstance(name, str) or not re.fullmatch(rf"{_ID}(?:\s*\.\s*{_ID}){{0,2}}", name.strip()):
        raise ValueError("Use a table or view name, optionally qualified by schema or catalog.")
    return ".".join(_quote(_unquote(part)) for part in re.findall(_ID, name.strip()))


def _column(columns, name, role):
    if not isinstance(name, str) or not name:
        raise ValueError(f"Use a {role} column name.")
    for col, kind, *_ in columns:
        if col.translate(_FOLD) == name.translate(_FOLD):
            if kind.id not in _KEY_TYPES:
                raise ValueError(f"Unsupported diff {role} type {kind} for {name!r}.")
            ident = _quote(col)
            # Use a common binary order independent of either relation's text collation.
            return (f"encode({ident})" if kind.id == "varchar" else ident), kind
    raise ValueError(f"Unknown {role} {name!r}.")


def diff_rows(connection, before: str, after: str, key: str, *, after_key=None, value=None, limit=5) -> dict:
    """Compare counts and optionally the multiset of values in one column per key."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer.")
    left, right = _relation(before), _relation(after)
    lc = connection.execute(f"SELECT * FROM {left} LIMIT 0").description
    rc = connection.execute(f"SELECT * FROM {right} LIMIT 0").description
    lk, lt = _column(lc, key, "key")
    rk, rt = _column(rc, key if after_key is None else after_key, "key")
    if lt != rt or str(lt) != str(rt):
        raise ValueError(f"Diff key types must match: {lt} in {before}, {rt} in {after}.")
    counts = "__sqlwhy_diff"
    # Avoid shadowing a supplied table when it appears inside the generated CTE.
    while _quote(counts) in (left.lower(), right.lower()):
        counts += "_"
    display = "decode(k)" if lt.id == "varchar" else "k"
    left_cols, right_cols = f"{lk} AS k", rk
    group = "k"
    extra = ""
    if value is not None:
        lv, lvt = _column(lc, value, "value")
        rv, rvt = _column(rc, value, "value")
        if lvt != rvt or str(lvt) != str(rvt):
            raise ValueError(f"Diff value types must match: {lvt} in {before}, {rvt} in {after}.")
        left_cols += f", {lv} AS v"
        right_cols += f", {rv}"
        group = "k, v"
    grouped = f"""
        SELECT {group}, count(*) FILTER (WHERE old) AS b, count(*) FILTER (WHERE NOT old) AS a
        FROM (SELECT {left_cols}, true AS old FROM {left}
              UNION ALL SELECT {right_cols}, false FROM {right}) AS inputs
        GROUP BY {group}
    """
    changed = "b <> a"
    values_only = "false"
    if value is not None:
        v = "decode(v)" if lvt.id == "varchar" else "v"
        witness = f"first(struct_pack(value := {v}, before_rows := b, after_rows := a) ORDER BY v NULLS FIRST)"
        grouped = f"""SELECT k, sum(b) AS b, sum(a) AS a, bool_or(b <> a) AS different,
                        {witness} FILTER (WHERE b > a) AS lost,
                        {witness} FILTER (WHERE a > b) AS gained
                      FROM ({grouped}) AS bins GROUP BY k"""
        changed, values_only = "different", "b = a AND different"
        extra = ", decreased_value := lost, increased_value := gained"
    result = connection.execute(f"""
        WITH {counts} AS ({grouped})
        SELECT coalesce(sum(b), 0), coalesce(sum(a), 0),
               count(*) FILTER (WHERE b = 0), count(*) FILTER (WHERE a = 0),
               count(*) FILTER (WHERE b > 0 AND a > b), count(*) FILTER (WHERE a > 0 AND a < b),
               count(*) FILTER (WHERE {values_only}),
               ARRAY(SELECT struct_pack(key := {display}, before_rows := b, after_rows := a{extra})
                     FROM {counts} WHERE {changed} ORDER BY k NULLS FIRST LIMIT ?)
        FROM {counts}
    """, [min(limit, _BIGINT_MAX)]).fetchone()
    b, a, added, removed, increased, decreased, modified, examples = result
    report = {
        "before_rows": b, "after_rows": a, "added_keys": added, "removed_keys": removed,
        "increased_keys": increased, "decreased_keys": decreased,
        "changed_keys": added + removed + increased + decreased + modified, "examples": _json(examples),
    }
    if value is not None:
        report.update(value_column=value, modified_keys=modified)
    return report
