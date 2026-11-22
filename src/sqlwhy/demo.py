import duckdb

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
}


def open_demo():
    con = duckdb.connect(config=SAFE_CONFIG)
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
