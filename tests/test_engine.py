import json
from datetime import date, datetime

import duckdb
import pytest

from sqlwhy.engine import trace_row


@pytest.fixture
def db():
    with duckdb.connect() as con:
        con.execute("create table orders as from (values (42,1),(42,2),(42,3),(NULL,4)) t(id,line)")
        con.execute("create table payments as from (values (1,true),(1,false),(1,NULL),(3,true)) t(line,ok)")
        yield con


@pytest.mark.parametrize(("kind", "unknown", "preserved"), [("inner", 1, 0), ("left", 2, 1)])
def test_join_and_where_report_mixed_results(db, kind, unknown, preserved):
    sql = f"""
        select o.id, o.line from orders o {kind} join payments p on o.line = p.line
        where p.ok and o.line > 0
    """
    trace = trace_row(db, sql, "orders.id", 42)
    source, join, where = trace["stages"]
    assert trace["status"] == "present"
    assert trace["output_rows"] == len(db.execute(sql).fetchall()) == 2
    assert source["after"] == 3
    assert join["join_kind"] == kind
    assert join["matched_source_rows"] == 2
    assert join["matched_pairs"] == 4
    assert join["unmatched_source_rows"] == 1
    assert join["preserved_unmatched_rows"] == preserved
    assert join["removed_source_rows"] == 1 - preserved
    assert where["before"] == 4 + preserved
    assert where["truth_counts"] == {"TRUE": 2, "FALSE": 1, "UNKNOWN": unknown}
    assert where["samples"][2] == {
        "outcome": "FALSE", "inputs": {'"p"."ok"': False, '"o"."line"': 1},
    }
    assert {"outcome": "UNKNOWN", "inputs": {'"p"."ok"': None, '"o"."line"': 1}} in where["samples"]
    assert ("NULLs on the right" in join["detail"]) == bool(preserved)


def test_missing_source_and_null_key_are_distinct(db):
    sql = "select * from orders o left join payments p on o.line=p.line where p.ok"
    absent = trace_row(db, sql, "o.id", 43)
    removed = trace_row(db, sql, "id", None)
    assert absent["status"] == "source_absent"
    assert all(stage["after"] == 0 for stage in absent["stages"])
    assert removed["status"] == "removed"
    assert removed["stages"][-1]["truth_counts"] == {"TRUE": 0, "FALSE": 0, "UNKNOWN": 1}


def test_quoted_key_matches_as_source_type_and_avoids_cte_collision(db):
    db.execute('create table "__sqlwhy_rows" as from (values (\'0042\'), (\'42\')) t("Key Value")')
    sql = 'select "Key Value" from "__sqlwhy_rows" "o.alias" where "Key Value" = \'0042\''
    assert trace_row(db, sql, '"o.alias"."Key Value"', 42)["status"] == "removed"
    assert trace_row(db, sql, '"Key Value"', "0042")["status"] == "present"


def test_key_cannot_resolve_to_joined_table(db):
    sql = "select * from orders o left join payments orders on o.line=orders.line"
    with pytest.raises(ValueError, match="source table"):
        trace_row(db, sql, "orders.id", 42)


def test_original_projection_is_bound(db):
    with pytest.raises(duckdb.BinderException, match="missing_column"):
        trace_row(db, "select missing_column from orders", "id", 42)


def test_samples_are_bounded_sorted_and_json_serializable(db):
    db.execute("""
        create table events as
        select date '2026-11-21' as day, timestamp '2026-11-21' + i * interval '1 second' as stamp
        from range(20, 0, -1) t(i)
    """)
    trace = trace_row(
        db, "select day from events where stamp < timestamp '2026-11-21 00:00:10'",
        "day", date(2026, 11, 21),
    )
    where = trace["stages"][-1]
    assert trace["target"]["value"] == "2026-11-21"
    assert where["truth_counts"] == {"TRUE": 9, "FALSE": 11, "UNKNOWN": 0}
    assert len(where["samples"]) == 6
    assert [row["inputs"]['"stamp"'] for row in where["samples"][:3]] == [
        datetime(2026, 11, 21, 0, 0, sec).isoformat() for sec in (1, 2, 3)
    ]
    json.dumps(trace, allow_nan=False)


def test_value_is_bound_and_caller_transaction_stays_open(db):
    value = "O'Reilly #42"
    db.execute("create table names (id varchar)")
    db.execute("begin")
    try:
        db.execute("insert into names values (?)", [value])
        assert trace_row(db, "select * from names", "id", value)["status"] == "present"
    finally:
        db.execute("rollback")
    assert db.sql("select count(*) from names").fetchone() == (0,)
    assert db.sql("select count(*) from orders").fetchone() == (4,)
