"""Validate and extract the supported read-only DuckDB SELECT shape."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

import duckdb
from sqlglot import Dialect, exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType as TT

_DUCKDB = Dialect.get_or_raise("duckdb")
_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_SCALARS = (
    exp.Identifier, exp.Column, exp.Literal, exp.Null, exp.Boolean, exp.Paren,
    exp.And, exp.Or, exp.Not, exp.Is, exp.EQ, exp.NEQ, exp.NullSafeEQ, exp.NullSafeNEQ,
    exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In, exp.Like, exp.ILike, exp.Escape,
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod, exp.Neg,
    exp.Cast, exp.TryCast, exp.DataType,
)
_CAST_TYPES = {
    exp.DType.BOOLEAN, exp.DType.INT, exp.DType.BIGINT, exp.DType.DOUBLE, exp.DType.FLOAT,
    exp.DType.TEXT, exp.DType.VARCHAR, exp.DType.DATE, exp.DType.TIME, exp.DType.TIMESTAMP,
    exp.DType.TIMESTAMPNTZ, exp.DType.TIMESTAMPTZ,
}


class UnsupportedSQLShape(ValueError):
    """The query is outside the SQL shapes that sqlwhy can trace or audit."""


@dataclass(frozen=True, slots=True)
class Query:
    sql: str
    statement_sql: str
    projections: tuple[exp.Expr, ...]
    base_sql: str
    base_alias: str
    base_name: str
    join_sql: str | None
    right_sql: str | None
    right_alias: str | None
    right_name: str | None
    join_kind: Literal["inner", "left"] | None
    on_sql: str | None
    where_sql: str | None
    where_columns: tuple[str, ...]
    where_expr: exp.Expr | None
    base_qualified: bool = False
    right_qualified: bool = False


def _render(node: exp.Expr) -> str:
    return node.sql(dialect="duckdb", identify=True, comments=False)


def _extra(node, allowed):
    return next((k.rstrip("_").upper() for k, v in node.args.items() if v and k not in allowed), None)


def _table(node, clause, tokens):
    if (
        not isinstance(node, exp.Table)
        or any(not isinstance(p, exp.Identifier) or tokens.get(p.meta.get("start")) == TT.STRING
               for p in node.parts)
        or _extra(node, {"this", "db", "catalog", "alias"})
    ):
        raise UnsupportedSQLShape(f"{clause} must name a table without derived sources, readers or table modifiers")
    alias = node.args.get("alias")
    if alias and (not isinstance(alias.this, exp.Identifier) or _extra(alias, {"this"})):
        raise UnsupportedSQLShape(f"{clause} does not support column aliases on a table")
    return node


def _column(node, star=False):
    return (isinstance(node, exp.Column) and isinstance(node.this, exp.Star if star else exp.Identifier)
            and not _extra(node, {"this", "table"}))


def _scalar(expr, clause, sql):
    for node in expr.walk():
        if isinstance(node, exp.Column) and not _column(node):
            raise UnsupportedSQLShape(f"{clause} column references may use one source alias as qualifier")
        if isinstance(node, exp.DataType) and (node.this not in _CAST_TYPES or node.expressions):
            raise UnsupportedSQLShape(f"{clause} only supports simple built-in cast types")
        if isinstance(node, exp.In) and _extra(node, {"this", "expressions"}):
            raise UnsupportedSQLShape(f"{clause} IN only supports a list of scalar expressions")
        if type(node) in _SCALARS:
            continue
        if isinstance(node, exp.AggFunc):
            raise UnsupportedSQLShape(f"{clause} does not support aggregate functions")
        if isinstance(node, exp.Window):
            raise UnsupportedSQLShape(f"{clause} does not support window functions")
        if isinstance(node, exp.Func):
            start, end = node.meta.get("start"), node.meta.get("end")
            name = sql[start:end + 1] if start is not None and end is not None else node.sql_name()
            raise UnsupportedSQLShape(f"{clause} does not support the {name.upper()} function")
        raise UnsupportedSQLShape(f"{clause} does not support {type(node).__name__} expressions")


def _unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _check_on(node):
    node = _unparen(node)
    if isinstance(node, exp.And):
        _check_on(node.this)
        _check_on(node.expression)
    elif not isinstance(node, exp.EQ) or not all(
        _column(_unparen(op)) for op in (node.this, node.expression)
    ):
        raise UnsupportedSQLShape("JOIN ON must compare two columns for equality, with optional AND conditions")


def parse_query(sql: str, *, connection=None, prefix="") -> Query:
    """Extract one SELECT while preserving the original ON and WHERE semantics."""
    try:
        tokens = _DUCKDB.tokenize(sql)
        if tokens and tokens[-1].token_type == TT.SEMICOLON:
            tokens.pop()
        if not tokens or any(t.token_type == TT.SEMICOLON for t in tokens):
            raise ValueError("Expected exactly one SELECT statement")
        stmt, = _DUCKDB.parser().parse(tokens, sql)
    except ParseError as e:
        raise ValueError(f"Invalid DuckDB SQL: {e.errors[0]['description']}") from e
    except TokenError as e:
        raise ValueError(f"Invalid DuckDB SQL: {e}") from e

    if isinstance(stmt, exp.SetOperation):
        raise UnsupportedSQLShape("SELECT set operations are unsupported")
    if isinstance(stmt, exp.Select) and stmt.args.get("with_"):
        raise UnsupportedSQLShape("SELECT WITH clauses are unsupported")
    if not isinstance(stmt, exp.Select):
        error_type = UnsupportedSQLShape if isinstance(stmt, (exp.Query, exp.Values)) else ValueError
        raise error_type("Expected a plain SELECT statement")
    if tokens[0].token_type != TT.SELECT:
        raise UnsupportedSQLShape("Expected a plain SELECT statement")
    extra = _extra(stmt, {"from_", "joins", "expressions", "where"})
    if extra:
        raise UnsupportedSQLShape(f"SELECT {extra} clauses are unsupported")
    src = stmt.args.get("from_")
    if not src or _extra(src, {"this"}):
        raise UnsupportedSQLShape("SELECT must have one base table in FROM")

    token_types = {t.start: t.token_type for t in tokens}
    base = _table(src.this, "FROM", token_types)
    joins = stmt.args.get("joins", [])
    if len(joins) > 1:
        raise UnsupportedSQLShape("Only one JOIN is supported")
    join = joins[0] if joins else None
    right, kind = None, None
    names = {base.alias_or_name.translate(_FOLD)}
    if join:
        if join.args.get("using"):
            raise UnsupportedSQLShape("JOIN USING is unsupported")
        if join.method:
            raise UnsupportedSQLShape(f"{join.method} JOIN is unsupported")
        if _extra(join, {"this", "side", "kind", "on"}):
            raise UnsupportedSQLShape("JOIN modifiers are unsupported")
        if (join.side, join.kind) in {("", ""), ("", "INNER")}:
            kind = "inner"
        elif (join.side, join.kind) in {("LEFT", ""), ("LEFT", "OUTER")}:
            kind = "left"
        else:
            raise UnsupportedSQLShape("Only INNER JOIN and LEFT JOIN are supported")
        right = _table(join.this, "JOIN", token_types)
        names.add(right.alias_or_name.translate(_FOLD))
        if len(names) != 2:
            raise ValueError("JOIN sources must have different aliases")
        _check_on(join.args.get("on"))

    if not stmt.expressions:
        raise ValueError("SELECT must have a projection")
    aliases = set()
    for proj in stmt.expressions:
        expr = proj
        if isinstance(proj, exp.Alias):
            aliases.add(proj.alias.translate(_FOLD))
            expr = proj.this
        star = expr if isinstance(expr, exp.Star) else expr.this if isinstance(expr, exp.Column) else None
        if proj is expr and isinstance(star, exp.Star):
            if _extra(star, set()):
                raise UnsupportedSQLShape("SELECT wildcard modifiers are unsupported")
            if expr is not star and not _column(expr, star=True):
                raise UnsupportedSQLShape("SELECT wildcards may use one source alias as qualifier")
        else:
            _scalar(expr, "SELECT", sql)

    where = stmt.args.get("where")
    where_columns = ()
    if where:
        _scalar(where.this, "WHERE", sql)
        cols = list(where.find_all(exp.Column, bfs=False))
        if any(not c.table and c.name.translate(_FOLD) in aliases for c in cols):
            raise UnsupportedSQLShape("WHERE may not reference an unqualified SELECT alias")
        where_columns = tuple(dict.fromkeys(map(_render, cols)))

    # A two-part name can bind to a STRUCT field or a table alias. Ask DuckDB
    # only after validating the SELECT, and use DESCRIBE so no rows are read.
    for col in stmt.find_all(exp.Column):
        if col.table and col.table.translate(_FOLD) not in names:
            if connection is not None:
                try:
                    connection.execute(f"DESCRIBE {prefix}{sql[:tokens[-1].end + 1]}")
                except duckdb.Error as err:
                    raise ValueError(str(err)) from err
            raise UnsupportedSQLShape(f"sqlwhy cannot trace the dotted reference {_render(col)} in this query.")

    # SQLGlot can normalize DuckDB syntax with different semantics, such as IN lists.
    last_table = right or base
    last_end = max(part.meta["end"] for part in last_table.find_all(exp.Identifier))
    start = next((i for i, t in enumerate(tokens) if t.start > last_end), len(tokens))
    stop = next((i for i in range(start, len(tokens))
                 if tokens[i].token_type == TT.WHERE and tokens[i - 1].token_type != TT.DOT), len(tokens))
    on_sql = sql[tokens[start + 1].start:tokens[stop - 1].end + 1] if join else None
    where_sql = sql[tokens[stop + 1].start:tokens[-1].end + 1] if where else None
    right_sql = _render(right) if right else None
    return Query(
        sql=sql, statement_sql=sql[:tokens[-1].end + 1], projections=tuple(stmt.expressions),
        base_sql=_render(base), base_alias=base.alias_or_name, base_name=base.name,
        join_sql=f"{kind.upper()} JOIN {right_sql} ON {on_sql}" if kind else None,
        right_sql=right_sql, right_alias=right.alias_or_name if right else None,
        right_name=right.name if right else None, join_kind=kind, on_sql=on_sql,
        where_sql=where_sql, where_columns=where_columns, where_expr=where.this if where else None,
        base_qualified=len(base.parts) > 1, right_qualified=right is not None and len(right.parts) > 1,
    )


def projected_key(plan: Query, key_name: str, source_names, right_names, output_names) -> int:
    """Find the first unambiguous direct projection of the left-source key."""
    return _projected_keys(plan, (key_name,), source_names, right_names, output_names)[0]


def _projected_keys(plan: Query, key_names, source_names, right_names, output_names) -> tuple[int, ...]:
    keys = {name.translate(_FOLD) for name in key_names}
    base = plan.base_alias.translate(_FOLD)
    origins = []
    for proj in plan.projections:
        expr = _unparen(proj.this if isinstance(proj, exp.Alias) else proj)
        if isinstance(expr, exp.Star):
            origins.extend(name.translate(_FOLD) in keys for name in source_names)
            origins.extend(False for _ in right_names)
        elif isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star):
            if expr.table.translate(_FOLD) == base:
                origins.extend(name.translate(_FOLD) in keys for name in source_names)
            else:
                origins.extend(False for _ in right_names)
        else:
            origins.append(isinstance(expr, exp.Column) and expr.name.translate(_FOLD) in keys
                           and (not expr.table or expr.table.translate(_FOLD) == base))

    if len(origins) != len(output_names):
        raise ValueError("Audit could not determine the report's projected columns.")
    matches = [i for i, is_key in enumerate(origins) if is_key]
    if not matches:
        raise UnsupportedSQLShape("Audit requires the source key to be projected directly in SELECT.")
    names = [name.translate(_FOLD) for name in output_names]
    counts = Counter(names)
    unique = tuple(i for i in matches if counts[names[i]] == 1)
    if not unique:
        raise UnsupportedSQLShape("Audit found an ambiguous projected source key. Give a direct key projection a unique name.")
    return unique


def needs_native_guard(expr, types):
    """Only project compound filters when their atoms require no fallible operation."""
    if expr is None or expr.find(exp.And, exp.Or, exp.Between, exp.In) is None:
        return False

    def family(node):
        if isinstance(node, exp.Column):
            kind = types.get(_render(node))
            if kind in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "FLOAT", "DOUBLE"}:
                return "number"
            return kind
        if isinstance(node, exp.Literal):
            return "VARCHAR" if node.is_string else "number"
        if isinstance(node, exp.Boolean):
            return "BOOLEAN"
        return None

    def safe(node):
        node = _unparen(node)
        if isinstance(node, (exp.And, exp.Or)):
            return safe(node.this) and safe(node.expression)
        if isinstance(node, exp.Not):
            return safe(node.this)
        if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            return isinstance(node.this, exp.Column)
        if isinstance(node, (exp.Between, exp.In)):
            options = [node.args["low"], node.args["high"]] if isinstance(node, exp.Between) else node.expressions
            left = family(node.this)
            return left in {"BOOLEAN", "VARCHAR", "number"} and all(
                isinstance(item, exp.Null) or family(item) == left for item in options
            )
        if isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            left = family(node.this)
            return left in {"BOOLEAN", "VARCHAR", "number"} and left == family(node.expression)
        return family(node) == "BOOLEAN"

    return not safe(expr)
