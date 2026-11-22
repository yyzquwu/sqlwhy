# sqlwhy

**Two orders go in. Two rows come out. Both rows are order 41.** Give sqlwhy a DuckDB query and a source key to see which join duplicated an order or which filter dropped it.

## Try it

From a checkout, using Python 3.11 or later in a virtual environment:

```sh
python -m pip install .
sqlwhy demo flat-counts
```

```text
Source rows: 2  |  Report rows: 2

Order 41
  LEFT JOIN "payments" AS "p" ON o.id = p.order_id  [1 -> 2]
    The join produced 2 matched pairs and preserved 0 unmatched source rows with NULLs on the right.

Order 42
  WHERE p.status = 'settled'  [1 -> 0]
    WHERE kept 0 of 1 row. 0 evaluated to FALSE and 1 to UNKNOWN (SQL NULL).
    UNKNOWN with "p"."status" = NULL
```

Order 41 matches two settled payments. Order 42 survives the left join with a NULL payment status, then drops out at the filter. Add `--json` to see every stage for both orders. Run `sqlwhy demo`, `sqlwhy demo unmatched-key` or `sqlwhy demo date-boundary` to inspect a single order.

## Trace your own query

```sh
sqlwhy examples/report.sql --table orders=examples/orders.csv --table payments=examples/payments.csv --trace o.id=42
sqlwhy report.sql --db warehouse.duckdb --trace orders.id=42 --json
```

`--db` opens an existing DuckDB file read-only. Alternatively, repeat `--table name=path` to import individual local CSV or Parquet files into memory for this invocation. CSV uses DuckDB type inference. Parquet retains its stored types. File mode loads the supplied tables each time, so a DuckDB file is more convenient for repeated traces on larger data.

Use an unqualified source key or qualify it with the alias of the left table. An unambiguous base-table name also works. Bare `NULL` traces SQL NULL. Leading-zero IDs such as `0042` stay text. DuckDB casts the supplied value to the type of the source column.

The key belongs to the **leftmost source**. A key can identify several rows, and a join can multiply them. The counts are for all rows carrying that key. A WHERE stage reports separate TRUE, FALSE and UNKNOWN counts and example predicate inputs. An explanation identifies what the SQL did; deciding whether the query or the underlying data needs to change is up to the analyst.

DuckDB can skip a conversion inside `AND`, `OR`, `BETWEEN`, or `IN`. For example, `valid AND CAST(amount AS INTEGER) > 0` can discard an invalid record before reading its amount. When a guarded operation could fail, sqlwhy runs the native filter, reports its exact before/after counts, and marks discarded rows `UNCLASSIFIED`. The JSON uses `null` for the unavailable FALSE/UNKNOWN breakdown and omits samples for that filter.

### Keep a regression check

```sh
sqlwhy examples/report.sql --table orders=examples/orders.csv --table payments=examples/payments.csv --trace o.id=41 --expect-rows 1
```

That command exits **0**. Change the payments file to `examples/payments_fanout.csv` and the same check exits **1** with `Expected row count 1, got 2.` Add `--json` to capture the trace on stdout while the mismatch goes to stderr.

`--expect-rows N` checks the exact output count for one source key. Zero is valid. Use `--expect present|removed|source_absent` to check status, or combine both flags to check both. For example, `--expect removed --expect-rows 0` also requires that the key existed in the source. Without an expectation, a completed trace exits **0**. Invalid input and unsupported SQL exit **2**. Expectations apply to single-key traces, including the single-order demos.

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

This release handles a plain DuckDB SELECT from one source table, optionally followed by one INNER or LEFT equijoin and a row-level WHERE condition. The displayed stages follow the query's logical semantics. DuckDB can choose a different physical execution order. For compound conditions the evidence describes the whole predicate. CTEs, aggregation, window functions, DISTINCT, ORDER BY, LIMIT, and arbitrary SQL functions are outside this release. The CLI reports unsupported syntax explicitly.

The trace measures row survival before projection. It does not evaluate the SELECT list for runtime conversion errors. Use deterministic source relations: repeated reads of a volatile view can produce different rows. The command is intended for trusted analytical queries and local inputs. The Python API uses the connection supplied by its caller. Trace output may include source values, so review it before attaching it to an issue.

## What happened on real SQL fixes?

MIT Open Learning fixed a blank discussion chart after finding that two tables used different names for the same platform. On synthetic tables, sqlwhy traced the original report query unchanged and showed the target disappearing at its inner join. A second published example hit a real limit. DuckDB executed the Stash issue reporter's query, but its `SELECT DISTINCT` prevents sqlwhy from tracing it.

We selected ten public fixes involving missing or duplicated rows. Isolating the affected joins and filters made all ten pre-fix examples traceable. Two repaired examples still require SQL that v0.2 cannot trace. Each case records the source, the reductions made, and the observed counts. The study measures behavior on selected fixtures. It does not estimate coverage of arbitrary SQL. [Ten-case results and sources](research/results.md)

Run `python research/replay.py` to reproduce the table offline. Run `python research/replay.py --check-templates` to fetch the four pinned dbt models and verify their raw parser results. Their compiled SQL remains untested. A small next extension to test is an equality between a joined column and a literal in `ON`. That would cover the repaired stock query in the PrestaShop fixture. The dbt models also use CTEs.

## Development

```sh
python -m pip install -e ".[test]"
python -m pytest -q
```

MIT licensed.
