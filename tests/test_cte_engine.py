"""Compare CTE lineage and counts with the original DuckDB report."""

from collections import Counter

import duckdb
import pytest

import sqlwhy.audit as audit
from sqlwhy import audit_rows, trace_row


@pytest.fixture
def con():
    with duckdb.connect() as conn:
        conn.execute("""
            CREATE TABLE orders AS FROM (VALUES (1, 'A'), (1, 'A'), (2, 'B'), (NULL, 'A'), (3, 'A')) v(id, region);
            CREATE TABLE payments AS FROM (VALUES (1, 'paid'), (1, 'pending'), (2, 'paid')) v(id, status);
            CREATE TABLE shipped AS FROM (VALUES (1, 'sent'), (1, 'pending'), (NULL, 'sent')) v(id, status);
        """)
        yield conn


REPORT = '''
-- Keep the physical key's duplicates and NULLs until the joins and filters.
WITH "Eligible Orders" AS (
    SELECT o.id AS "order key", o.region FROM orders o WHERE o.region = 'A'
), "paid" AS (
    SELECT e."order key" AS id, p.status
    FROM "Eligible Orders" e LEFT JOIN payments p ON e."order key" = p.id
    WHERE p.status IS NULL OR p.status = 'paid'
)
SELECT b.id AS "report key", s.status
FROM paid AS b LEFT JOIN shipped s ON b.id = s.id
WHERE s.status IS NULL OR s.status IN ('sent', 'pending');
'''


def test_cte_audit_and_trace_match_native_sql(con):
    report = Counter(row[0] for row in con.execute(REPORT).fetchall())
    source = Counter(row[0] for row in con.execute("SELECT id FROM orders").fetchall())
    result = audit_rows(con, REPORT, "o.id", 0)
    assert (result["source_rows"], result["source_keys"], result["report_rows"]) == (5, 4, sum(report.values()))
    examples = [(key, report[key]) for key in (None, 1, 2, 3) if report[key] != 0]
    assert result["violation_count"] == len(examples)
    assert [(trace["target"]["value"], trace["output_rows"]) for trace in result["examples"]] == examples
    for key in (None, 1, 2, 3, 4):
        trace = trace_row(con, REPORT, "o.id", key)
        assert trace["output_rows"] == report[key]
        assert trace["stages"][0]["after"] == source[key]
        assert trace["status"] == ("present" if report[key] else "removed" if key in source else "source_absent")
        if report[key]:
            assert trace == next(example for example in result["examples"] if example["target"]["value"] == key)


def test_keeps_where_and_join_evidence_after_cte_drops_key(con):
    trace = trace_row(con, REPORT, "id", 2)
    stages = trace["stages"]
    assert [(s["cte"], s["stage"], s["before"], s["after"]) for s in stages] == [
        ("Eligible Orders", "source", None, 1), ("Eligible Orders", "where", 1, 0),
        ("paid", "join", 0, 0), ("paid", "where", 0, 0),
        (None, "join", 0, 0), (None, "where", 0, 0),
    ]
    assert stages[1]["clause"] == "WHERE o.region = 'A'"
    assert stages[1]["samples"] == [{"outcome": "FALSE", "inputs": {'"o"."region"': "B"}}]
    assert 'e."order key" = p.id' in stages[2]["clause"]
    assert stages[-1]["truth_counts"] == {"TRUE": 0, "FALSE": 0, "UNKNOWN": 0}


@pytest.mark.parametrize(("sql", "step", "reason"), [
    ("WITH renamed AS (SELECT id + 0 AS id FROM orders) SELECT id FROM renamed", "renamed", "projected directly"),
    ("WITH renamed AS (SELECT id AS key FROM orders) SELECT key + 0 AS id FROM renamed", "final SELECT", "projected directly"),
    ("WITH renamed AS (SELECT id, id AS ID FROM orders) SELECT * FROM renamed", "renamed", "ambiguous"),
    ("WITH totals AS (SELECT count(*) AS id FROM orders) SELECT id FROM totals", "totals", "aggregate"),
    ("WITH renamed AS (SELECT id FROM orders) SELECT DISTINCT id FROM renamed", "final SELECT", "DISTINCT"),
])
def test_both_entry_points_reject_unsupported_steps(con, sql, step, reason):
    for run in (lambda: trace_row(con, sql, "id", 1), lambda: audit_rows(con, sql, "id", 1, limit=0)):
        with pytest.raises(ValueError) as failure:
            run()
        assert step.casefold() in str(failure.value).casefold()
        assert reason.casefold() in str(failure.value).casefold()


def test_original_projection_runs_when_audit_returns_only_totals(con):
    sql = """
        WITH exposed AS (SELECT id, region FROM orders)
        SELECT id, CAST(region AS INTEGER) FROM exposed
    """
    with pytest.raises(duckdb.ConversionException):
        audit_rows(con, sql, "id", 1, limit=0)


def test_generated_ctes_do_not_shadow_physical_or_user_cte_names(con):
    con.execute("""
        CREATE TABLE __sqlwhy_rows(id INTEGER);
        INSERT INTO __sqlwhy_rows VALUES (1), (2);
        CREATE TABLE __sqlwhy_report(id INTEGER);
        INSERT INTO __sqlwhy_report VALUES (1), (1);
        CREATE TABLE __sqlwhy_counts(id INTEGER);
        INSERT INTO __sqlwhy_counts VALUES (1);
        CREATE TABLE __sqlwhy_unmatched(id INTEGER);
        INSERT INTO __sqlwhy_unmatched VALUES (1);
    """)
    sql = """
        WITH __sqlwhy_source AS (
            SELECT p.id FROM __sqlwhy_rows p JOIN __sqlwhy_report r ON p.id = r.id
        ), __sqlwhy_totals AS (
            SELECT s.id FROM __sqlwhy_source s JOIN __sqlwhy_counts c ON s.id = c.id
        )
        SELECT t.id FROM __sqlwhy_totals t JOIN __sqlwhy_unmatched u ON t.id = u.id
    """
    result = audit_rows(con, sql, "p.id", 1)
    assert (result["source_rows"], result["report_rows"], result["violation_count"]) == (2, 2, 2)
    assert [example["output_rows"] for example in result["examples"]] == [2, 0]


def test_qualified_physical_relation_with_the_same_name_as_a_cte(con):
    sql = """
        WITH orders AS (SELECT id FROM main.orders WHERE region = 'A')
        SELECT a.id FROM orders a JOIN main.orders b ON a.id = b.id
    """
    result = audit_rows(con, sql, "id", 1)
    assert (result["source_rows"], result["report_rows"], result["violation_count"]) == (5, 5, 3)
    assert [trace["output_rows"] for trace in result["examples"]] == [0, 4, 0]


def test_key_precision_and_read_only_transaction(con):
    con.execute("""
        CREATE TABLE precise(k DECIMAL(38, 18));
        INSERT INTO precise VALUES (11345678901234567890.012345678901234567), (11345678901234567890.012345678901234568);
        BEGIN TRANSACTION READ ONLY;
    """)
    sql = """
        WITH renamed AS (SELECT k AS code FROM precise)
        SELECT code FROM renamed WHERE code = 11345678901234567890.012345678901234568
    """
    result = audit_rows(con, sql, "k", 1)
    example, = result["examples"]
    assert example["target"]["value"] == "11345678901234567890.012345678901234567"
    assert (result["source_keys"], result["report_rows"], example["output_rows"]) == (2, 1, 0)
    con.commit()


def test_counts_all_cte_violations_with_bounded_example_traces(con, monkeypatch):
    con.execute("CREATE TABLE big AS SELECT i AS id FROM range(10000) t(i)")
    seen = []
    original = audit._trace_chain

    def record(connection, chain, key, value, prepared):
        seen.append(value)
        return original(connection, chain, key, value, prepared)

    monkeypatch.setattr(audit, "_trace_chain", record)
    sql = "WITH kept AS (SELECT id FROM big WHERE FALSE) SELECT id FROM kept"
    result = audit_rows(con, sql, "id", 1, limit=2)
    assert result["violation_count"] == 10000
    assert seen == [0, 1]
    assert audit_rows(con, sql, "id", 1, limit=0) == {**result, "examples": []}
    assert seen == [0, 1]


def test_follows_each_direct_key_alias_until_final_projection(con):
    sql = """
        WITH renamed AS (SELECT id AS first, id AS "other key" FROM orders),
        selected AS (SELECT "other key" AS tracked FROM renamed)
        SELECT tracked FROM selected WHERE tracked IS NOT NULL
    """
    result = audit_rows(con, sql, "id", 1)
    assert (result["report_rows"], result["violation_count"]) == (4, 2)
    assert [trace["output_rows"] for trace in result["examples"]] == [0, 2]
    assert result["examples"][1] == trace_row(con, sql, "id", 1)


def test_cte_automatic_deduplication_cannot_switch_the_tracked_key(con):
    sql = """
        WITH renamed AS (SELECT 11 AS a, 22 AS a, id AS a_1 FROM orders)
        SELECT {key} FROM renamed
    """
    for run in (lambda report: trace_row(con, report, "id", 1),
                lambda report: audit_rows(con, report, "id", 1, limit=0)):
        with pytest.raises(ValueError, match="Final SELECT.*projected directly"):
            run(sql.format(key="a_1"))
    fixed = sql.format(key="a_1_1")
    result = audit_rows(con, fixed, "id", 1)
    assert (result["source_rows"], result["report_rows"], result["violation_count"]) == (5, 5, 1)
    assert result["examples"] == [trace_row(con, fixed, "id", 1)]


def test_stage_conversion_error_labels_the_cte(con):
    con.execute("CREATE TABLE payloads AS FROM (VALUES (1, '2'), (2, 'bad')) v(id, payload)")
    sql = """
        WITH ordered AS (SELECT id FROM payloads WHERE CAST(payload AS INTEGER) > 0)
        SELECT id FROM ordered WHERE id = 1
    """
    assert con.execute(sql).fetchall() == [(1,)]
    assert audit_rows(con, sql, "id", 1, limit=0)["violation_count"] == 1
    for run in (lambda: trace_row(con, sql, "id", 2), lambda: audit_rows(con, sql, "id", 1)):
        with pytest.raises(duckdb.ConversionException, match='CTE "ordered"'):
            run()
