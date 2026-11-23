from pathlib import Path

import pytest

from sqlwhy.cli import main

ISSUE = "https://github.com/yyzquwu/sqlwhy/issues/new?template=unsupported-sql.yml"
DIFF = "sqlwhy diff BEFORE AFTER --db ... --key ..."


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("orders.csv").write_text("id\n42\n", encoding="utf-8")
    Path("ok.sql").write_text("SELECT id FROM orders", encoding="utf-8")
    Path("sqlwhy.toml").write_text(
        'version = 1\n[tables]\norders = "orders.csv"\n'
        '[[checks]]\nname = "First audit"\ntype = "audit"\nsql = "ok.sql"\nkey = "id"\nexpect_rows = 1\n'
        '[[checks]]\nname = "Report grain"\ntype = "audit"\nsql = "report.sql"\nkey = "id"\nexpect_rows = 1\n',
        encoding="utf-8",
    )
    return ["--table", "orders=orders.csv", "--json"]


def cli_error(args, capsys):
    with pytest.raises(SystemExit) as err:
        main(args)
    output = capsys.readouterr()
    assert err.value.code == 2
    assert output.out == ""
    return output.err


def test_top_level_help_lists_and_exemplifies_each_command(capsys):
    with pytest.raises(SystemExit) as err:
        main(["--help"])
    out = capsys.readouterr()
    assert err.value.code == 0 and not out.err
    overview, examples = out.out.split("Examples\n", 1)
    assert "Commands\n" in overview
    for name, meaning, example in (
        ("QUERY.sql", "Trace one source key", "report.sql"),
        ("audit", "Check expected row counts", "audit report.sql"),
        ("diff", "Compare saved outputs", "diff previous_orders current_orders"),
        ("check", "Run a TOML suite", "check sqlwhy.toml"),
        ("demo", "Try a built-in example", "demo cte-audit"),
    ):
        assert any(line.split()[:1] == [name] and meaning in line for line in overview.splitlines())
        assert f"  sqlwhy {example}" in examples


@pytest.mark.parametrize("cmd", ["trace", "audit", "check"])
def test_unsupported_cte_names_clause_and_suggests_diff(cmd, inputs, capsys):
    Path("report.sql").write_text(
        "WITH base AS (SELECT id FROM orders), grouped AS (SELECT id FROM base GROUP BY id) "
        "SELECT id FROM grouped",
        encoding="utf-8",
    )
    if cmd == "check":
        args = ["check", "sqlwhy.toml", "--json"]
    elif cmd == "audit":
        args = ["audit", "report.sql", "--key", "id", "--expect-rows", "1", *inputs]
    else:
        args = ["report.sql", "--trace", "id=42", *inputs]
    err = cli_error(args, capsys)
    context = "Check 'Report grain': " if cmd == "check" else ""
    assert f"{context}CTE 'grouped': SELECT GROUP clauses are unsupported" in err
    assert "tables or views" in err and DIFF in err and ISSUE in err


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT DISTINCT id FROM orders", "SELECT DISTINCT clauses are unsupported"),
        ("WITH base AS NOT MATERIALIZED (SELECT id FROM orders) SELECT * FROM base", "CTE column lists"),
        (
            "WITH base AS (SELECT id + 1 AS id FROM orders) SELECT * FROM base",
            "CTE 'base': This step requires the source key to be projected directly in SELECT.",
        ),
    ],
)
def test_unsupported_plain_query_cte_header_and_key_projection(sql, message, inputs, capsys):
    Path("report.sql").write_text(sql, encoding="utf-8")
    err = cli_error(["report.sql", "--trace", "id=42", *inputs], capsys)
    assert message in err and DIFF in err and ISSUE in err


@pytest.mark.parametrize(
    ("sql", "key", "message"),
    [
        (None, "id=42", "report.sql"),
        (b"\xff", "id=42", "codec can't decode"),
        (b"SELECT id FROM orders", "missing-equals", "expected KEY=VALUE"),
        (b"SELECT id FROM orders", "missing=42", "Binder Error"),
        (b"SELECT id FROM absent", "id=42", "Table with name absent does not exist"),
        (b"SELECT id FROM", "id=42", "Invalid DuckDB SQL"),
        (b"CREATE TABLE example (id INTEGER)", "id=42", "Expected a plain SELECT"),
        (b"SELECT 1; SELECT 2", "id=42", "Expected exactly one SELECT"),
        (b"WITH base AS", "id=42", "CTE 'base': expected AS (SELECT ...)"),
    ],
)
def test_unrelated_trace_errors_omit_fallback(sql, key, message, inputs, capsys):
    if sql is not None:
        Path("report.sql").write_bytes(sql)
    err = cli_error(["report.sql", "--trace", key, *inputs], capsys)
    assert message in err and DIFF not in err and ISSUE not in err


def test_check_runtime_error_keeps_context_without_fallback(inputs, capsys):
    Path("report.sql").write_text("SELECT id FROM absent", encoding="utf-8")
    err = cli_error(["check", "sqlwhy.toml", "--json"], capsys)
    assert "Check 'Report grain': Catalog Error: Table with name absent does not exist" in err
    assert DIFF not in err and ISSUE not in err
