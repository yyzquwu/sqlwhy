"""Compare the explanation to the original SELECT, including duplicate source keys."""
from collections import Counter

import duckdb
import pytest

from sqlwhy.engine import trace_row


@pytest.fixture
def db():
    with duckdb.connect() as con:
        con.execute("create table orders(id integer, region varchar)")
        con.execute("insert into orders values (1,'A'), (1,'B'), (2,'A'), (3,NULL), (4,'B')")
        con.execute("create table payments(order_id integer, status varchar)")
        con.execute("insert into payments values (1,'paid'), (1,'pending'), (2,NULL), (4,'paid'), (4,'paid')")
        yield con


@pytest.mark.parametrize("kind", ["inner", "left"])
@pytest.mark.parametrize("condition", [
    "", "where p.status = 'paid'", "where p.status is null",
    "where o.region = 'B' and p.status = 'paid'",
])
def test_trace_counts_match_the_original_select(db, kind, condition):
    sql = f"select o.id from orders o {kind} join payments p on p.order_id = o.id {condition}"
    counts = Counter(row[0] for row in db.execute(sql).fetchall())
    for key in (1, 2, 3, 4, 5):
        result = trace_row(db, sql, "o.id", key)
        assert result["stages"][-1]["after"] == counts[key]
        assert result["status"] == ("source_absent" if key == 5 else "present" if counts[key] else "removed")


def test_flat_total_can_hide_a_lost_key():
    with duckdb.connect() as con:
        con.execute("create table orders as from (values (41),(42)) t(id)")
        con.execute("create table payments as from (values (41),(41)) t(order_id)")
        sql = "select o.id from orders o join payments p on o.id = p.order_id"
        assert con.execute(sql).fetchall() == [(41,), (41,)]
        assert con.sql("select count(*) from orders").fetchone()[0] == 2
        assert trace_row(con, sql, "o.id", 42)["status"] == "removed"
        assert trace_row(con, sql, "o.id", 41)["stages"][-1]["after"] == 2
