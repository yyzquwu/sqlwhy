import json
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "audit"
TABLES = ["--table", "orders=orders.csv", "--table", "payments=payments.csv"]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "sqlwhy", *args], cwd=EXAMPLE, capture_output=True,
                          text=True, encoding="utf-8", timeout=20)


def test_packaged_demo_matches_the_file_audit():
    demo = cli("demo", "cte-audit", "--json")
    audit = cli("audit", "cte_broken.sql", *TABLES, "--key", "o.id", "--expect-rows", "1", "--json")
    assert demo.returncode == audit.returncode == 1 and not demo.stderr and not audit.stderr
    result = json.loads(audit.stdout)
    assert json.loads(demo.stdout) == result
    assert (result["source_keys"], result["report_rows"], result["violation_count"]) == (2, 2, 2)
    changes = [(trace["target"]["value"], s["cte"], s["stage"], s["before"], s["after"])
               for trace in result["examples"] for s in trace["stages"]
               if s["before"] is not None and s["before"] != s["after"]]
    assert changes == [(41, "orders_with_payments", "join", 1, 2), (42, "settled_orders", "where", 1, 0)]
    text = cli("demo", "cte-audit")
    assert "[orders_with_payments] LEFT JOIN" in text.stdout and "[settled_orders] WHERE" in text.stdout


def test_fixed_report_and_direct_trace():
    audit = cli("audit", "cte_report.sql", *TABLES, "--key", "o.id", "--expect-rows", "1", "--json")
    assert audit.returncode == 0 and json.loads(audit.stdout)["violation_count"] == 0
    trace = cli("cte_broken.sql", *TABLES, "--trace", "o.id=42", "--expect", "removed")
    assert trace.returncode == 0 and "[settled_orders] WHERE" in trace.stdout


@pytest.mark.parametrize("command", [["audit", "--key", "o.id", "--expect-rows", "1", "--limit", "0"],
                                      ["trace", "--trace", "o.id=41"]])
def test_grain_change_names_the_step_even_without_examples(command, tmp_path):
    file = tmp_path / "grouped.sql"
    file.write_text("WITH lines AS (SELECT o.id FROM orders o), "
                    "per_order AS (SELECT id, count(*) AS n FROM lines GROUP BY id) SELECT * FROM per_order",
                    encoding="utf-8")
    mode, *args = command
    p = cli(*(["audit"] if mode == "audit" else []), str(file), *TABLES, *args, "--json")
    assert p.returncode == 2 and not p.stdout
    assert "per_order" in p.stderr and "group" in p.stderr.lower()
