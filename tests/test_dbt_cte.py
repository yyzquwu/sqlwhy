from pathlib import Path

import duckdb
import pytest

from sqlwhy import audit_rows, trace_row


FIXTURES = Path(__file__).resolve().parents[1] / "research" / "dbt"


def test_compiled_stg_orders_audit(tmp_path):
    original = (FIXTURES / "compiled/stg_orders.sql").read_text(encoding="utf-8")
    injected = (FIXTURES / "stg_orders_injected.sql").read_text(encoding="utf-8")
    assert injected.split("\n", 2)[2].rstrip() == original.replace(
        "    from source", "    from source\n    where status = 'completed'", 1
    ).rstrip()

    with duckdb.connect(str(tmp_path / "evidence.duckdb")) as con:
        con.execute("CREATE TABLE raw_orders (id INTEGER, user_id INTEGER, order_date DATE, status VARCHAR)")
        con.execute("COPY raw_orders FROM ? (HEADER)", [str(FIXTURES / "raw_orders.csv")])
        for sql, report_rows, order_rows in ((original, 99, 1), (injected, 67, 0)):
            native = con.execute(f"""
                SELECT count(*), count(*) FILTER (WHERE order_id = 1) FROM ({sql}) AS report
            """).fetchone()
            assert native == (report_rows, order_rows)
            assert [col[0] for col in con.execute(f"SELECT * FROM ({sql}) LIMIT 0").description] == [
                "order_id", "customer_id", "order_date", "status"
            ]
            trace = trace_row(con, sql, "raw_orders.id", 1)
            audit = audit_rows(con, sql, "raw_orders.id", 1, limit=1)
            assert trace["output_rows"] == order_rows
            assert (audit["source_keys"], audit["report_rows"], audit["violation_count"]) == (
                99, report_rows, 99 - report_rows
            )
            assert audit["examples"] == ([] if order_rows else [trace])

        stage = trace["stages"][-1]
        assert trace["status"] == "removed"
        assert (stage["cte"], stage["clause"], stage["before"], stage["after"]) == (
            "renamed", "WHERE status = 'completed'", 1, 0
        )
        assert stage["truth_counts"] == {"TRUE": 0, "FALSE": 1, "UNKNOWN": 0}


@pytest.mark.parametrize(
    "case, key, projection, rows, diagnostic",
    [
        ("a1", "invoice_id", "source_relation, total_current_payment", [("alpha", 60), ("beta", 40)],
         r"CTE 'invoice_linked': FROM must reference preceding CTE 'invoices'"),
        ("a2", "bill_payment_id", "transaction_id, transaction_type", [(2, "credit"), (2, "debit")],
         r"CTE 'bill_payment_lines': FROM must reference preceding CTE 'bill_payments'"),
        ("a3", "checkout_id", "code, total_abandoned_checkout_discount_amount", [("save5", 10)],
         r"CTE 'abandoned_checkout'.*WHERE.*COALESCE"),
        ("a5", "ticket_id", "ticket_id, first_resolution_calendar_minutes", [(1, None), (2, 15)],
         r"CTE 'ticket': FROM must reference preceding CTE 'historical_solved_status'"),
    ],
    ids=["a1-quickbooks-invoice", "a2-quickbooks-bill-payment", "a3-shopify", "a5-zendesk"],
)
def test_compiled_pre_fix_models_run_in_duckdb(case, key, projection, rows, diagnostic, tmp_path):
    sql = (FIXTURES / "compiled" / f"{case}.sql").read_text(encoding="utf-8")
    with duckdb.connect(str(tmp_path / "evidence.duckdb")) as con:
        con.execute((FIXTURES / "synthetic_staging.sql").read_text(encoding="utf-8"))
        assert con.execute(f"SELECT {projection} FROM ({sql}) AS report ORDER BY 1, 2").fetchall() == rows
        with pytest.raises(ValueError, match=diagnostic):
            audit_rows(con, sql, key, 1)
