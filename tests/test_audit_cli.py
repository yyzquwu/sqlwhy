import json
import os
import subprocess
import sys

import duckdb
import pytest


def cli(*args):
    return subprocess.run([sys.executable, "-m", "sqlwhy", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


@pytest.fixture
def audit_files(tmp_path):
    sql, orders, payments = (tmp_path / name for name in ("report.sql", "orders.csv", "payments.csv"))
    sql.write_text("SELECT o.id FROM orders o LEFT JOIN payments p ON o.id = p.order_id WHERE p.posted",
                   encoding="utf-8")
    orders.write_text("id\n1\n2\n3\n", encoding="utf-8")
    payments.write_text("order_id,posted\n1,true\n1,true\n3,true\n", encoding="utf-8")
    return sql, ["--table", f"orders={orders}", "--table", f"payments={payments}"]


def test_audit_detects_violations_when_totals_match(audit_files):
    sql, tables = audit_files
    result = cli("audit", sql, *tables, "--key", "o.id", "--expect-rows", 1, "--json")
    assert result.returncode == 1 and not result.stderr
    data = json.loads(result.stdout)
    assert data["key"] == "o.id" and data["expected_rows"] == 1
    assert data["source_rows"] == data["source_keys"] == data["report_rows"] == 3
    assert data["violation_count"] == 2
    examples = {ex["target"]["value"]: ex for ex in data["examples"]}
    assert {key: ex["output_rows"] for key, ex in examples.items()} == {1: 2, 2: 0}
    assert [stage["after"] for stage in examples[1]["stages"]] == [1, 2, 2]
    assert [stage["after"] for stage in examples[2]["stages"]] == [1, 1, 0]


def test_audit_text_shows_source_deviation_and_changed_clauses(audit_files):
    sql, tables = audit_files
    sql.with_name("orders.csv").write_text("id\n1\n1\n", encoding="utf-8")
    sql.with_name("payments.csv").write_text("order_id,posted\n1,true\n1,true\n1,\n", encoding="utf-8")
    result = cli("audit", sql, *tables, "--key", "o.id", "--expect-rows", 1)
    assert result.returncode == 1 and not result.stderr
    assert "Violations 1  |  Examples shown 1" in result.stdout
    assert "o.id = 1  [expected 1, got 4]" in result.stdout
    source, join, where = result.stdout.splitlines()[-3:]
    assert 'FROM "orders"' in source and "source count already differs: 2" in source
    assert 'LEFT JOIN "payments"' in join and "[2 -> 6]" in join
    assert "WHERE p.posted" in where and "[6 -> 4]  FALSE 0, UNKNOWN 2 (SQL NULL)" in where


def test_audit_text_labels_native_filter_rejections(tmp_path):
    sql, amounts = tmp_path / "report.sql", tmp_path / "amounts.csv"
    sql.write_text("SELECT id FROM amounts WHERE valid AND CAST(amount AS INTEGER) > 0", encoding="utf-8")
    amounts.write_text("id,valid,amount\n42,false,n/a\n42,true,9\n42,true,-1\n", encoding="utf-8")
    result = cli("audit", sql, "--table", f"amounts={amounts}", "--key", "id", "--expect-rows", 3)
    assert result.returncode == 1 and not result.stderr
    assert "[3 -> 1]  2 unclassified" in result.stdout


def test_passing_audit_leaves_database_unchanged(tmp_path):
    sql, db = tmp_path / "report.sql", tmp_path / "orders audit.duckdb"
    sql.write_text("SELECT id FROM orders", encoding="utf-8")
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE orders AS SELECT id FROM (VALUES (7), (7), (NULL), (NULL)) AS t(id)")
    original = db.read_bytes()
    result = cli("audit", sql, "--db", db, "--key", "orders.id", "--expect-rows", 2, "--json")
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {
        "key": "orders.id", "expected_rows": 2, "source_rows": 4, "source_keys": 2,
        "report_rows": 4, "violation_count": 0, "examples": [],
    }
    assert db.read_bytes() == original


@pytest.mark.parametrize(("limit", "shown"), [(None, 5), (2, 2), (0, 0)])
def test_audit_limit_never_changes_violation_count(tmp_path, limit, shown):
    sql, orders = tmp_path / "report.sql", tmp_path / "orders.csv"
    sql.write_text("SELECT id FROM orders WHERE FALSE", encoding="utf-8")
    orders.write_text("id\n1\n2\n3\n4\n5\n6\n7\n", encoding="utf-8")
    args = [] if limit is None else ["--limit", limit]
    result = cli("audit", sql, "--table", f"orders={orders}", "--key", "id", "--expect-rows", 1,
                 "--json", *args)
    assert result.returncode == 1 and not result.stderr
    data = json.loads(result.stdout)
    assert data["violation_count"] == 7 and len(data["examples"]) == shown


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["--key", "o.id"], "--expect-rows"),
        (["--expect-rows", "1"], "--key"),
        (["--key", "o.id", "--expect-rows", "-1"], "nonnegative integer"),
        (["--key", "o.id", "--expect-rows", "1", "--limit", "-1"], "nonnegative integer"),
        (["--key", "o.id=1", "--expect-rows", "1"], "source column"),
    ],
)
def test_invalid_audit_writes_only_stderr(audit_files, args, error):
    sql, tables = audit_files
    result = cli("audit", sql, *tables, "--json", *args)
    assert result.returncode == 2 and not result.stdout
    assert error in result.stderr


def test_audit_help_is_discoverable():
    assert "sqlwhy audit" in cli("--help").stdout
    result = cli("audit", "--help")
    assert result.returncode == 0 and not result.stderr
    assert all(option in result.stdout for option in ("--key", "--expect-rows", "--limit", "--table", "--db"))


@pytest.mark.parametrize("expected,code", [(1, 0), (0, 1)])
def test_redirected_text_preserves_audit_exit_code(tmp_path, expected, code):
    sql, db = tmp_path / "report.sql", tmp_path / "orders.duckdb"
    sql.write_text('SELECT "订单" FROM orders WHERE "订单" IS NOT NULL', encoding="utf-8")
    with duckdb.connect(str(db)) as con:
        con.execute('CREATE TABLE orders AS SELECT \'東京\' AS "订单"')
    result = subprocess.run(
        [sys.executable, "-m", "sqlwhy", "audit", str(sql), "--db", str(db), "--key", "订单",
         "--expect-rows", str(expected)], capture_output=True, encoding="cp1252",
        env={**os.environ, "PYTHONIOENCODING": "cp1252:strict"}, timeout=20,
    )
    assert result.returncode == code and not result.stderr
    assert r"\u8ba2\u5355" in result.stdout
    if code:
        assert r"\u6771\u4eac" in result.stdout


def test_sample_order_breaks_collation_ties_before_limiting(tmp_path):
    sql, db = tmp_path / "report.sql", tmp_path / "events.duckdb"
    sql.write_text("SELECT id FROM events WHERE label = 'kept'", encoding="utf-8")
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE events(id INT, label VARCHAR COLLATE NOCASE)")
        con.executemany("INSERT INTO events VALUES (1, ?)", [(s,) for s in ("aa", "aA", "Aa", "AA")])
    result = cli("audit", sql, "--db", db, "--key", "id", "--expect-rows", 1, "--json")
    assert result.returncode == 1 and not result.stderr
    samples = json.loads(result.stdout)["examples"][0]["stages"][-1]["samples"]
    assert [s["inputs"]['"label"'] for s in samples] == ["AA", "Aa", "aA"]
