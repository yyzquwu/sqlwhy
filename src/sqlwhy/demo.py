import duckdb

from .engine import trace_row
from .files import SAFE_CONFIG

DEMOS = {
    "left-join-null": (
        "A WHERE filter drops the NULL-extended row from a LEFT JOIN.",
        """SELECT o.id FROM orders AS o
        LEFT JOIN payments AS p ON o.id = p.order_id
        WHERE p.status = 'paid'""",
    ),
    "unmatched-key": (
        "An INNER JOIN drops an order with no matching payment.",
        """SELECT o.id FROM orders AS o
        INNER JOIN payments AS p ON o.id = p.order_id""",
    ),
    "date-boundary": (
        "An exclusive timestamp cutoff drops an order placed exactly at midnight.",
        """SELECT o.id FROM orders AS o
        WHERE o.created_at < TIMESTAMP '2026-11-21 00:00:00'""",
    ),
    "flat-counts": (
        "A doubled order and a dropped order leave the total unchanged.",
        """SELECT o.id FROM orders AS o
        LEFT JOIN payments AS p ON o.id = p.order_id
        WHERE p.status = 'settled'""",
    ),
}


def open_demo(name="left-join-null"):
    con = duckdb.connect(config=SAFE_CONFIG)
    if name == "flat-counts":
        con.execute("""
            CREATE TABLE orders (id INTEGER);
            INSERT INTO orders VALUES (41), (42);
            CREATE TABLE payments (order_id INTEGER, status VARCHAR);
            INSERT INTO payments VALUES (41, 'settled'), (41, 'settled');
        """)
        return con
    con.execute("""
        CREATE TABLE orders (id INTEGER, created_at TIMESTAMP);
        INSERT INTO orders VALUES
            (17, TIMESTAMP '2026-11-20 23:59:59'),
            (42, TIMESTAMP '2026-11-21 00:00:00'),
            (99, TIMESTAMP '2026-11-21 00:00:01');
        CREATE TABLE payments (order_id INTEGER, status VARCHAR);
        INSERT INTO payments VALUES (17, 'paid'), (99, 'pending');
    """)
    return con


def trace_flat_counts(con):
    sql = DEMOS["flat-counts"][1]
    return {
        "source_rows": con.execute("SELECT count(*) FROM orders").fetchone()[0],
        "result_rows": len(con.execute(sql).fetchall()),
        "traces": [trace_row(con, sql, "o.id", key) for key in (41, 42)],
    }
