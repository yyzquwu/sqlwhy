"""Time the grain audit on an in-memory report with offsetting grain violations."""

import platform
from statistics import median
from time import perf_counter

import duckdb
import sqlglot

from sqlwhy.audit import audit_rows

KEYS = 100_000
REPEATS = 7
SQL = """SELECT o.id AS report_key FROM orders o
LEFT JOIN payments p ON o.id = p.order_id
WHERE p.status = 'settled'"""


def run():
    print(f"{platform.platform()} | {platform.processor()}")
    print(f"Python {platform.python_version()} | DuckDB {duckdb.__version__} | SQLGlot {sqlglot.__version__}")
    with duckdb.connect(config={"threads": 1}) as con:
        start = perf_counter()
        con.execute("CREATE TABLE orders AS SELECT id FROM range(?) t(id)", [KEYS])
        con.execute("""CREATE TABLE payments AS
            SELECT id AS order_id, 'settled' AS status FROM orders WHERE id % 100 <> 1
            UNION ALL SELECT id, 'settled' FROM orders WHERE id % 100 = 0""")
        setup = perf_counter() - start
        print(f"{KEYS:,} source keys and rows | {KEYS:,} payment rows | setup {setup * 1000:.1f} ms")

        native = dict(con.execute(f"SELECT report_key, count(*) FROM ({SQL}) r GROUP BY report_key").fetchall())
        assert sum(native.values()) == KEYS
        assert sum(native.get(key, 0) != 1 for key in range(KEYS)) == 2_000
        print(f"2,000 violating keys | one warmup per mode | {REPEATS} measured runs per mode | one DuckDB thread")

        for limit in (0, 5):
            audit_rows(con, SQL, "o.id", 1, limit=limit)
            elapsed = []
            for _ in range(REPEATS):
                start = perf_counter()
                result = audit_rows(con, SQL, "o.id", 1, limit=limit)
                elapsed.append((perf_counter() - start) * 1000)
                assert result["source_rows"] == result["source_keys"] == result["report_rows"] == KEYS
                assert result["violation_count"] == 2_000
                examples = result["examples"]
                assert len(examples) == limit
                if limit:
                    assert [t["target"]["value"] for t in examples] == [0, 1, 100, 101, 200]
                    assert [t["output_rows"] for t in examples] == [2, 0, 2, 0, 2]
                    assert [s["after"] for s in examples[0]["stages"]] == [1, 2, 2]
                    assert [s["after"] for s in examples[1]["stages"]] == [1, 1, 0]
            print(f"limit {limit} | median {median(elapsed):.1f} ms | runs (ms) "
                  + ", ".join(f"{value:.1f}" for value in elapsed))


if __name__ == "__main__":
    run()
