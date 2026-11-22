from dataclasses import FrozenInstanceError

import duckdb
import pytest

from sqlwhy.query import parse_query


def test_plain_select_and_trailing_comment():
    sql = """-- report
    SELECT * FROM main."Order Detail" AS "order ""row"
    WHERE "order ""row"."Item ID" IN (42, NULL)
      AND "order ""row"."Item ID" IS NOT NULL;
    -- end
    """
    query = parse_query(sql)
    assert query.sql == sql
    assert query.base_name == "Order Detail"
    assert query.base_alias == 'order "row'
    assert query.base_sql == '"main"."Order Detail" AS "order ""row"'
    assert query.join_sql is query.join_kind is query.right_name is None
    assert query.where_columns == ('"order ""row"."Item ID"',)
    with pytest.raises(FrozenInstanceError):
        query.base_name = "different"


@pytest.mark.parametrize(("syntax", "kind"), [
    ("JOIN", "inner"), ("INNER JOIN", "inner"),
    ("LEFT JOIN", "left"), ("LEFT OUTER JOIN", "left"),
])
def test_join_fields_and_multikey_predicate(syntax, kind):
    on = "(o.id = p.order_id AND (o.region) = (p.region))"
    where = "p.status IS NULL OR o.created_at < TIMESTAMP '2026-11-21 00:00:00'"
    query = parse_query(
        f"SELECT o.*, p.status AS state FROM orders o {syntax} main.payments p "
        f"ON {on} WHERE {where}"
    )
    assert (query.base_alias, query.base_name) == ("o", "orders")
    assert (query.join_kind, query.right_alias, query.right_name) == (kind, "p", "payments")
    assert query.right_sql == '"main"."payments" AS "p"'
    assert query.join_sql == f'{kind.upper()} JOIN {query.right_sql} ON {on}'
    assert query.on_sql == on
    assert query.where_sql == where
    assert query.where_columns == ('"p"."status"', '"o"."created_at"')


def test_predicate_retains_duckdb_list_membership_and_comments():
    query = parse_query("""SELECT id FROM items WHERE
        id IN [1, NULL] -- DuckDB list membership
        OR note = 'one; two' ; -- trailing comment
    """)
    assert query.where_columns == ('"id"', '"note"')
    assert query.where_sql == "id IN [1, NULL] -- DuckDB list membership\n        OR note = 'one; two'"
    with duckdb.connect() as con:
        con.execute("CREATE TABLE items AS SELECT 2 AS id, 'three' AS note")
        assert con.execute(f"SELECT {query.where_sql} FROM {query.base_sql}").fetchone() == (False,)


@pytest.mark.parametrize(("sql", "reason"), [
    ("SELECT 1", "FROM"),
    ("", "exactly one"),
    ("SELECT * FROM t; SELECT * FROM u", "exactly one"),
    ("SELECT * FROM t;;", "exactly one"),
    ("FROM t", "plain SELECT"),
    ("TABLE t", "plain SELECT"),
    ("DELETE FROM t", "plain SELECT"),
    ("WITH c AS (SELECT * FROM t) SELECT * FROM c", "WITH"),
    ("SELECT * FROM t UNION SELECT * FROM u", "set operations"),
    ("SELECT DISTINCT * FROM t", "DISTINCT"),
    ("SELECT count(*) FROM t", "aggregate"),
    ("SELECT row_number() OVER () FROM t", "window"),
    ("SELECT id FROM t GROUP BY id", "GROUP"),
    ("SELECT * FROM t ORDER BY id", "ORDER"),
    ("SELECT * FROM t LIMIT 10", "LIMIT"),
    ("SELECT * FROM t OFFSET 1", "OFFSET"),
    ("SELECT * FROM t USING SAMPLE 1 ROWS", "SAMPLE"),
    ("SELECT * FROM t AS x(k)", "column aliases"),
    ("SELECT * FROM (SELECT * FROM t) AS x", "FROM"),
    ("SELECT * FROM read_csv('sample.csv')", "FROM"),
    ("SELECT * FROM 'sample.csv'", "FROM"),
    ("SELECT * FROM t NATURAL JOIN u", "NATURAL"),
    ("SELECT * FROM t LEFT JOIN u USING (id)", "USING"),
    ("SELECT * FROM t RIGHT JOIN u ON t.id = u.id", "INNER JOIN and LEFT JOIN"),
    ("SELECT * FROM t, u", "JOIN ON"),
    ("SELECT * FROM t JOIN u ON t.id >= u.id", "equality"),
    ("SELECT * FROM t JOIN u ON t.id = 1", "two columns"),
    ("SELECT * FROM t JOIN u ON t.id = u.id JOIN v ON t.id = v.id", "one JOIN"),
    ("SELECT * FROM t AS x JOIN u AS X ON x.id = X.id", "different aliases"),
    ("SELECT t.* EXCLUDE (id) FROM t", "wildcard modifiers"),
    ("SELECT UNNEST(vals) FROM t", "UNNEST"),
    ("SELECT * FROM t WHERE random() > 0.5", "RANDOM"),
    ("SELECT * FROM t WHERE id IN (SELECT id FROM u)", "WHERE IN"),
    ("SELECT id + 1 AS new_id FROM t WHERE NEW_ID = 2", "SELECT alias"),
    ("SELECT * FROM t WHERE main.t.id = 2", "source alias"),
])
def test_rejects_unsupported_sql(sql, reason):
    with pytest.raises(ValueError, match=reason):
        parse_query(sql)
