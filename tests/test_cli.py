import json

import duckdb
import pytest

from sqlwhy.cli import main


@pytest.mark.parametrize(
    ("name", "counts"),
    [
        ("left-join-null", [1, 1, 0]),
        ("unmatched-key", [1, 0]),
        ("date-boundary", [1, 0]),
    ],
)
def test_demo_traces(name, counts, capsys):
    assert main(["demo", name, "--json", "--expect", "removed"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "removed"
    assert result["target"] == {"key": "o.id", "value": 42}
    assert [stage["after"] for stage in result["stages"]] == counts


def test_default_demo_reports_null_evidence(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "LEFT JOIN" in out
    assert "p.status" in out or '"p"."status"' in out
    assert "UNKNOWN" in out and "NULL" in out


def test_unmet_expectation_returns_one(capsys):
    assert main(["demo", "--expect", "present"]) == 1
    output = capsys.readouterr()
    assert "removed" in output.out
    assert "Expected present, got removed." in output.err


@pytest.fixture
def query_and_db(tmp_path):
    sql = tmp_path / "orders.sql"
    sql.write_text("SELECT id FROM orders WHERE id = '0042' OR id IS NULL", encoding="utf-8")
    db = tmp_path / "order audit.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE orders (id VARCHAR)")
        con.execute("INSERT INTO orders VALUES ('0042'), ('42'), ('NULL'), (NULL)")
    return sql, db


@pytest.mark.parametrize(
    ("literal", "value", "status"),
    [("0042", "0042", "present"), ("42", 42, "removed"), ("NULL", None, "present"),
     ('"NULL"', "NULL", "removed"), ("99", 99, "source_absent")],
)
def test_database_trace_keeps_exact_identifier(literal, value, status, query_and_db, capsys):
    sql, db = query_and_db
    original = db.read_bytes()
    assert main([str(sql), "--db", str(db), "--trace", f"orders.id={literal}", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["value"] == value
    assert result["status"] == status
    assert db.read_bytes() == original


def test_missing_database_returns_two_and_is_not_created(tmp_path, capsys):
    sql = tmp_path / "orders.sql"
    sql.write_text("SELECT id FROM orders", encoding="utf-8")
    db = tmp_path / "missing.duckdb"
    with pytest.raises(SystemExit) as err:
        main([str(sql), "--db", str(db), "--trace", "orders.id=42"])
    assert err.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    assert "database file does not exist" in output.err
    assert not db.exists()


def test_repeatable_file_tables_are_used(tmp_path, capsys):
    sql = tmp_path / "orders.sql"
    sql.write_text("SELECT o.id FROM orders o JOIN payments p ON o.id = p.order_id", encoding="utf-8")
    orders, payments = tmp_path / "orders.csv", tmp_path / "payments.csv"
    orders.write_text("id\n42\n", encoding="utf-8")
    payments.write_text("order_id\n42\n", encoding="utf-8")
    assert main([str(sql), "--table", f"orders={orders}", "--table", f"payments={payments}",
                 "--trace", "o.id=42", "--expect", "present"]) == 0
    assert "Result  present" in capsys.readouterr().out
