"""Check grain audit results against native DuckDB grouping of the original SELECT."""

import json
from pathlib import Path

import duckdb
import pytest

from sqlwhy.audit import audit_rows
from sqlwhy.demo import DEMOS, open_demo
from sqlwhy.engine import trace_row


def assert_parity(con, sql, key, source_sql, report_key, expected_rows):
    source = con.execute(f"SELECT id, count(*) FROM ({source_sql}) s GROUP BY id ORDER BY id NULLS FIRST").fetchall()
    report = dict(con.execute(f"SELECT {report_key}, count(*) FROM ({sql}\n) r GROUP BY {report_key}").fetchall())
    violations = [(value, report.get(value, 0)) for value, _ in source if report.get(value, 0) != expected_rows]
    result = audit_rows(con, sql, key, expected_rows, limit=len(source))
    examples = result["examples"]
    assert result == {
        "key": key,
        "expected_rows": expected_rows,
        "source_rows": sum(n for _, n in source),
        "source_keys": len(source),
        "report_rows": sum(report.values()),
        "violation_count": len(violations),
        "examples": examples,
    }
    assert [(t["target"]["value"], t["output_rows"]) for t in examples] == violations
    assert examples == [trace_row(con, sql, key, value) for value, _ in violations]
    return result


def test_grain_catches_flat_counts_and_passes_after_both_fixes():
    sql = DEMOS["flat-counts"][1]
    with open_demo("flat-counts") as con:
        bad = assert_parity(con, sql, "o.id", "SELECT id FROM orders", "id", 1)
        assert bad["source_rows"] == bad["report_rows"] == 2
        assert bad["violation_count"] == 2
        assert [t["output_rows"] for t in bad["examples"]] == [2, 0]

        con.execute("DELETE FROM payments")
        con.execute("INSERT INTO payments VALUES (41, 'settled'), (42, 'settled')")
        good = assert_parity(con, sql, "o.id", "SELECT id FROM orders", "id", 1)
        assert good["source_rows"] == good["report_rows"] == 2
        assert good["violation_count"] == 0


@pytest.mark.parametrize("kind", ["INNER", "LEFT"])
@pytest.mark.parametrize("where", ["", "WHERE p.status = 'paid' AND o.region = 'A'", "WHERE p.status IS NULL"])
@pytest.mark.parametrize("expected_rows", [0, 1, 2])
def test_audit_matches_native_grouping_for_duplicate_and_null_keys(kind, where, expected_rows):
    sql = f"""SELECT o.id AS report_key, p.status FROM orders o
        {kind} JOIN payments p ON o.id = p.order_id {where}"""
    with duckdb.connect() as con:
        con.execute("""CREATE TABLE orders AS FROM (VALUES
            (1, 'A'), (1, 'B'), (2, 'A'), (3, NULL), (4, 'A'), (NULL, 'A'), (NULL, 'B')
        ) t(id, region)""")
        con.execute("""CREATE TABLE payments AS FROM (VALUES
            (1, 'paid'), (1, 'pending'), (2, NULL), (4, 'paid'), (4, 'paid'), (5, 'paid')
        ) t(order_id, status)""")
        result = assert_parity(con, sql, "o.id", "SELECT id FROM orders", "report_key", expected_rows)
        assert result["source_rows"] == 7
        assert result["source_keys"] == 5


def test_original_mit_open_learning_report_and_fixed_replay():
    fixture = Path(__file__).resolve().parents[1] / "research" / "cases" / "a4.json"
    case = json.loads(fixture.read_text(encoding="utf-8"))
    source_sql = "SELECT courserun_readable_id AS id FROM ol_warehouse_production_dimensional.tfact_discussion_events"
    with duckdb.connect() as con:
        con.execute(case["setup_sql"])
        before = assert_parity(con, case["source_sql"], case["key"], source_sql, case["original_output_key"], 1)
        assert (before["source_keys"], before["report_rows"], before["violation_count"]) == (2, 1, 1)
        trace, = before["examples"]
        assert trace["target"]["value"] == "1"
        assert trace["output_rows"] == 0
        assert [(s["stage"], s["after"]) for s in trace["stages"]] == [("source", 1), ("join", 0)]

        after = assert_parity(con, case["after_sql"], case["key"], source_sql, "id", 1)
        assert (after["source_keys"], after["report_rows"], after["violation_count"]) == (2, 2, 0)
