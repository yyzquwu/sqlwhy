# sqlwhy

**An order is in your source table. Where did your report lose it?** Give sqlwhy the query and a source key. It runs the supported join and filter on your data, shows how many rows carrying that key survive each stage, and displays the values behind the filter result.

## Try it

From a checkout, using Python 3.11 or later in a virtual environment:

```sh
python -m pip install .
sqlwhy demo
```

```text
Tracing o.id = 42
  FROM "orders" AS "o"  [1]
    Found 1 matching source row.
  LEFT JOIN "payments" AS "p" ON o.id = p.order_id  [1 -> 1]
    The join produced 0 matched pairs and preserved 1 unmatched source row with NULLs on the right.
  WHERE p.status = 'paid'  [1 -> 0]
    WHERE kept 0 of 1 row. 0 evaluated to FALSE and 1 to UNKNOWN (SQL NULL).
    UNKNOWN with "p"."status" = NULL
Result  removed
```

The demo traces order 42 through a left join to payments. The order survives the join with a NULL payment status. The comparison `p.status = 'paid'` then evaluates to UNKNOWN. SQL keeps only TRUE rows, so the order disappears at the filter. Run `sqlwhy demo unmatched-key` and `sqlwhy demo date-boundary` for two other ways to lose the same order.

## Trace your own query

```sh
sqlwhy examples/report.sql --table orders=examples/orders.csv --table payments=examples/payments.csv --trace o.id=42
sqlwhy report.sql --db warehouse.duckdb --trace orders.id=42 --json
```

`--db` opens an existing DuckDB file read-only. Alternatively, repeat `--table name=path` to import individual local CSV or Parquet files into memory for this invocation. CSV uses DuckDB type inference. Parquet retains its stored types. File mode loads the supplied tables each time, so a DuckDB file is more convenient for repeated traces on larger data.

Use an unqualified source key or qualify it with the left table?s alias. An unambiguous base-table name also works. Bare NULL traces SQL NULL; leading-zero IDs such as 0042 stay text. DuckDB casts the supplied value to the source column?s type.

The key belongs to the **leftmost source**. A key can identify several rows, and a join can multiply them. The counts are for all rows carrying that key. A WHERE stage reports separate TRUE, FALSE and UNKNOWN counts and example predicate inputs. An explanation identifies what the SQL did; deciding whether the query or the underlying data needs to change is up to the analyst.

### A row count can hide the bug

The sample has two orders. Substitute `examples/payments_fanout.csv` for `examples/payments.csv` in the first command. The report still returns two rows: both are order 41. Trace `o.id=41` to see the fan-out, then `o.id=42` to see the missing order. Both effects occur in the same query.

### Keep a regression check

```sh
sqlwhy report.sql --db warehouse.duckdb --trace orders.id=42 --expect present
```

With `--expect`, exit status **1** means the observed status differs from the expected one. A successful investigation returns **0** by default, even when the row was removed. Invalid input and unsupported SQL return **2**. The three possible statuses are `present`, `removed`, and `source_absent`. Use `--json` for machine-readable stage counts and evidence.

## Python API

```python
from pathlib import Path

import duckdb
from sqlwhy import trace_row

with duckdb.connect("warehouse.duckdb", read_only=True) as con:
    result = trace_row(con, Path("report.sql").read_text(), "orders.id", 42)
    print(result["status"], result["output_rows"])
```

## Scope

This release handles a plain DuckDB SELECT from one source table, optionally followed by one INNER or LEFT equijoin and a row-level WHERE condition. The displayed stages follow the query's logical semantics; DuckDB can choose a different physical execution order. For compound conditions the evidence describes the whole predicate. CTEs, aggregation, window functions, DISTINCT, ORDER BY, LIMIT, and arbitrary SQL functions are outside this first release. The CLI reports unsupported syntax explicitly.

The trace measures row survival before projection. It does not evaluate the SELECT list for runtime conversion errors. Use deterministic source relations: repeated reads of a volatile view can produce different rows. The command is intended for trusted analytical queries and local inputs. The Python API uses the connection supplied by its caller. Trace output may include source values, so review it before attaching it to an issue.

## Development

```sh
python -m pip install -e ".[test]"
python -m pytest -q
```

MIT licensed.
