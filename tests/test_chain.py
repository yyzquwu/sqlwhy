import duckdb
import pytest

from sqlwhy import audit_rows, trace_row
from sqlwhy.chain import parse_chain


def test_chain_preserves_original_duckdb_sql():
    sql = '''-- leading comment
    WiTh "Raw; ""Rows""" AS (/* comment with ) */
        SELECT p.id FROM products AS p WHERE p.id IN [1, NULL] -- body comment
    ) -- before first comma
    , Kept AS (
        SELECT src.id AS retained FROM "raw; ""rows""" src WHERE (src.id + 1) > 1
    ) /* before final SELECT */
    SeLeCt retained FROM kept; -- trailing comment
    '''
    first, second, final = parse_chain(sql)
    assert [s.name for s in (first, second, final)] == ['Raw; "Rows"', "Kept", None]
    assert first.prefix_sql == ""
    assert first.query.where_sql == "p.id IN [1, NULL]"
    assert second.prefix_sql == sql[:sql.index(", Kept")] + "\n"
    assert second.query.base_alias == "src"
    assert final.prefix_sql == sql[:sql.index("SeLeCt retained")] + "\n"
    with duckdb.connect() as con:
        con.execute("CREATE TABLE products AS SELECT * FROM (VALUES (1), (2), (NULL)) AS p(id)")
        for step in (first, second, final):
            assert con.execute(step.statement_sql).fetchall() == [(1,)]
        assert con.execute(sql).fetchall() == [(1,)]


def test_qualified_physical_tables_can_share_cte_names():
    sql = """WITH raw AS (
        SELECT r.* FROM main.raw r LEFT JOIN main.kept k ON r.id = k.id
    ), kept AS (SELECT * FROM raw) SELECT * FROM kept"""
    with duckdb.connect() as con:
        con.execute("CREATE TABLE raw AS SELECT 1 AS id")
        con.execute("CREATE TABLE kept AS SELECT 2 AS id")
        assert con.execute(parse_chain(sql)[-1].statement_sql).fetchall() == [(1,)]


def test_recursive_token_can_be_a_cte_identifier():
    sql = "WITH recursive AS (SELECT * FROM products) SELECT * FROM recursive"
    with duckdb.connect() as con:
        con.execute("CREATE TABLE products AS SELECT 1 AS id")
        first, final = parse_chain(sql)
        assert first.name == "recursive"
        assert con.execute(final.statement_sql).fetchall() == [(1,)]


def test_plain_select_is_left_to_legacy_parser():
    assert parse_chain("/* WITH c AS (SELECT 1) */ SELECT * FROM products") is None


@pytest.mark.parametrize("run", [trace_row, audit_rows])
def test_rejects_late_unsupported_clauses_before_database_access(run):
    sql = "WITH kept AS (SELECT id FROM products) SELECT id FROM kept ORDER BY id"
    with pytest.raises(ValueError, match="Final SELECT.*ORDER"):
        run(object(), sql, "id", 1)


@pytest.mark.parametrize("header", ["later AS MATERIALIZED", "later(id) AS"])
@pytest.mark.parametrize("run", [trace_row, audit_rows])
def test_earlier_body_precedes_unsupported_later_header(header, run):
    sql = f"WITH early AS (SELECT DISTINCT id FROM orders), {header} (SELECT id FROM early) SELECT * FROM later"
    with duckdb.connect() as con:
        con.execute("CREATE TABLE orders AS SELECT 1 AS id")
        assert con.execute(sql).fetchall() == [(1,)]
        with pytest.raises(ValueError, match="CTE 'early': SELECT DISTINCT"):
            run(con, sql, "id", 1)


@pytest.mark.parametrize(("sql", "reason"), [
    ("WITH RECURSIVE loop AS (SELECT * FROM t) SELECT * FROM loop", "loop.*RECURSIVE"),
    ("WITH a AS (SELECT * FROM t), \"A\" AS (SELECT * FROM a) SELECT * FROM a", "'A'.*duplicate"),
    ("WITH first(id) AS (SELECT * FROM t) SELECT * FROM first", "first.*column lists"),
    ("WITH first AS NOT MATERIALIZED (SELECT * FROM t) SELECT * FROM first", "first.*modifiers"),
    ("WITH \"Totals\" AS (SELECT count(*) FROM t) SELECT * FROM \"Totals\"", "Totals.*aggregate"),
    ("WITH ranked AS (SELECT row_number() OVER () FROM t) SELECT * FROM ranked", "ranked.*window"),
    ("WITH first AS (SELECT * FROM t WHERE id IN (SELECT id FROM u)) SELECT * FROM first", "first.*WHERE IN"),
    ("WITH first AS (WITH hidden AS (SELECT * FROM t) SELECT * FROM hidden) SELECT * FROM first", "first.*WITH"),
    ("WITH first AS (SELECT * FROM first) SELECT * FROM first", "first.*uses CTE name"),
    ("WITH a AS (SELECT * FROM b), b AS (SELECT * FROM a) SELECT * FROM b", "'a'.*uses CTE name 'b'"),
    ("WITH a AS (SELECT * FROM t), b AS (SELECT * FROM main.a) SELECT * FROM b",
     "'b'.*preceding CTE 'a'.*\"main\"\\.\"a\""),
    ("WITH a AS (SELECT * FROM t), b AS (SELECT * FROM a), c AS (SELECT * FROM a) SELECT * FROM c",
     "'c'.*preceding CTE 'b'"),
    ("WITH a AS (SELECT * FROM t), b AS (SELECT * FROM a) SELECT * FROM a", "Final SELECT.*preceding CTE 'b'"),
    ("WITH a AS (SELECT * FROM t) SELECT * FROM a x JOIN a y ON x.id = y.id", "Final SELECT.*lookup.*'a'"),
    ("WITH a AS (SELECT * FROM t JOIN b ON t.id = b.id), b AS (SELECT * FROM a) SELECT * FROM b",
     "'a'.*lookup.*'b'"),
    ("WITH a AS (SELECT * FROM t;) SELECT * FROM a", "'a'.*semicolon"),
    ("WITH a AS (SELECT * FROM t", "'a'.*closing parenthesis"),
    ("WITH a AS (SELECT * FROM t)", "'a'.*final SELECT"),
    ("WITH a AS (SELECT * FROM t) SELECT * FROM a; SELECT * FROM t", "Final SELECT.*exactly one"),
])
def test_chain_rejects_unsupported_sql_with_stage_name(sql, reason):
    with pytest.raises(ValueError, match=reason):
        parse_chain(sql)
