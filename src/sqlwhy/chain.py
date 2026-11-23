"""Plan a linear chain of ordinary DuckDB SELECTs without rewriting them."""

from dataclasses import dataclass

from sqlglot.errors import TokenError
from sqlglot.tokens import TokenType as TT

from .query import Query, UnsupportedSQLShape, _DUCKDB, _FOLD, parse_query


@dataclass(frozen=True, slots=True)
class ChainStep:
    name: str | None
    query: Query
    _sql: str
    _prefix_end: int

    @property
    def prefix_sql(self) -> str:
        return self._sql[:self._prefix_end] + "\n" if self._prefix_end else ""

    @property
    def statement_sql(self) -> str:
        return self.prefix_sql + self.query.statement_sql


def _label(name):
    return "Final SELECT" if name is None else f"CTE {name!r}"


def _step(sql, start, end, prefix_end, name):
    try:
        query = parse_query(sql[start:end])
    except ValueError as err:
        error_type = UnsupportedSQLShape if isinstance(err, UnsupportedSQLShape) else ValueError
        raise error_type(f"{_label(name)}: {err}") from err
    return ChainStep(name, query, sql, prefix_end)


def _body_end(tokens, start, name):
    depth = 1
    for i in range(start, len(tokens)):
        kind = tokens[i].token_type
        if kind == TT.L_PAREN:
            depth += 1
        elif kind == TT.R_PAREN:
            depth -= 1
            if not depth:
                return i
        elif kind == TT.SEMICOLON:
            raise ValueError(f"{_label(name)}: expected one SELECT body without a semicolon")
    raise ValueError(f"{_label(name)}: missing closing parenthesis")


def _plan(sql, spans, names):
    steps = []
    previous = None
    for span in spans:
        step = _step(sql, *span)
        q = step.query
        label = _label(step.name)
        base = q.base_name.translate(_FOLD)
        if previous is None:
            if not q.base_qualified and base in names:
                raise UnsupportedSQLShape(f"{label}: FROM uses CTE name {q.base_name!r}. Qualify the physical table.")
        elif q.base_qualified or base != previous.translate(_FOLD):
            raise UnsupportedSQLShape(f"{label}: FROM must reference preceding CTE {previous!r}, got {q.base_sql}")
        if q.right_name and not q.right_qualified and q.right_name.translate(_FOLD) in names:
            raise UnsupportedSQLShape(f"{label}: JOIN must use a physical lookup table, got CTE {q.right_name!r}")
        previous = step.name
        steps.append(step)
    return tuple(steps)


def parse_chain(sql: str) -> tuple[ChainStep, ...] | None:
    """Validate every SELECT and return ordered CTE steps followed by the final SELECT."""
    try:
        tokens = _DUCKDB.tokenize(sql)
    except TokenError as e:
        raise ValueError(f"Invalid DuckDB SQL: {e}") from e
    if not tokens or tokens[0].token_type != TT.WITH:
        return None
    if (len(tokens) > 2 and tokens[1].token_type == TT.RECURSIVE
            and tokens[2].token_type not in (TT.ALIAS, TT.L_PAREN)):
        raise UnsupportedSQLShape(f"{_label(tokens[2].text)}: WITH RECURSIVE is unsupported")

    spans, names = [], set()
    try:
        spans.extend(_spans(sql, tokens, names))
    except ValueError:
        # A malformed or unsupported header must not mask an earlier invalid step.
        _plan(sql, spans, names)
        raise
    return _plan(sql, spans, names)


def _spans(sql, tokens, names):
    i, prefix_end = 1, 0
    while i < len(tokens):
        token = tokens[i]
        name = token.text
        label = _label(name)
        raw = sql[token.start:token.end + 1]
        if not name or (token.token_type != TT.IDENTIFIER
                        and (raw.startswith("$") or not raw.replace("$", "_").isidentifier())):
            raise ValueError(f"{label}: expected a CTE identifier")
        folded = name.translate(_FOLD)
        if folded in names:
            raise ValueError(f"{label}: duplicate CTE name")
        names.add(folded)
        as_body = tokens[i + 1:i + 3]
        if [t.token_type for t in as_body] != [TT.ALIAS, TT.L_PAREN]:
            col_list = as_body and as_body[0].token_type == TT.L_PAREN
            modifier = as_body and as_body[0].token_type == TT.ALIAS and (
                [t.text.upper() for t in tokens[i + 2:i + 4]] in (["MATERIALIZED", "("], ["NOT", "MATERIALIZED"])
            )
            error_type = UnsupportedSQLShape if col_list or modifier else ValueError
            raise error_type(f"{label}: expected AS (SELECT ...). CTE column lists and modifiers are unsupported.")
        end = _body_end(tokens, i + 3, name)
        yield as_body[-1].end + 1, tokens[end].start, prefix_end, name
        i = end + 1
        if i == len(tokens):
            raise ValueError(f"{label}: expected a final SELECT")
        prefix_end = tokens[i].start
        if tokens[i].token_type != TT.COMMA:
            yield prefix_end, len(sql), prefix_end, None
            return
        i += 1
    raise ValueError("WITH requires a CTE before the final SELECT")
