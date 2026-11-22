from pathlib import Path

import duckdb
import pytest

from sqlwhy.files import open_tables


def test_import_csv_and_parquet_from_paths_with_spaces(tmp_path):
    csv = tmp_path / "orders 2026.csv"
    csv.write_text("id\n41\n42\n")
    pq = tmp_path / "payments=1.parquet"
    with duckdb.connect() as con:
        con.sql("select 41 as order_id, 'paid' as status").to_parquet(str(pq))
    with open_tables([f"orders={csv}", f"payments={pq}"]) as con:
        assert con.sql("select o.id, p.status from orders o left join payments p on o.id=p.order_id order by o.id").fetchall() == [(41, "paid"), (42, None)]
        assert con.sql("select current_setting('enable_external_access')").fetchone() == (False,)


def test_duplicate_names_are_rejected(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("id\n42\n")
    with pytest.raises(ValueError, match="Duplicate table name"):
        open_tables([f"orders={path}", f"Orders={path}"])


def test_examples_run_from_real_files():
    root = Path(__file__).resolve().parents[1] / "examples"
    with open_tables([f"{name}={root / (name + '.csv')}" for name in ("orders", "payments")]) as con:
        assert con.execute((root / "report.sql").read_text()).fetchall() == [(41, "settled")]
