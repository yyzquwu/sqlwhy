# sqlwhy 1.x contracts

The CLI runs locally. Use deterministic input tables or views built from the same data snapshot. A row-count or value difference describes the data; the expected grain and the business decision belong to the author of the check.

## Commands and exit codes

| Command | Exit 1 when | Evidence |
| --- | --- | --- |
| `sqlwhy report.sql --trace KEY=VALUE …` | An explicit status or row-count expectation fails | One key through its source, joins and filters |
| `sqlwhy audit report.sql --key COLUMN --expect-rows N …` | Any distinct source key produces a different count | Total violations plus bounded traces |
| `sqlwhy diff BEFORE AFTER --key COLUMN …` | A key's multiplicity changes, or its compared values change when `--value` is set | Complete per-category counts plus bounded example keys |
| `sqlwhy check project.toml` | Any named check fails | All completed checks in file order |

Exit 0 means completion with the supplied expectations met. Exit 2 means the command could not complete. For CLI JSON on completed runs, read stdout regardless of exit 0 or 1. On an input or execution error, read stderr. For a suite execution error, the CLI reports the failing check and emits no partial JSON report. Standalone traces can write expectation diagnostics to stderr on exit 1. `--limit 0` preserves totals and suppresses examples for audit, diff and check.

## Suite files

Use `version = 1` and exactly one input source: `database = "path.duckdb"` or a `[tables]` map of simple table names to CSV or Parquet paths. Paths to data and SQL are relative to the suite file. Check names must be unique. Unsupported fields are errors.

| Check type | Required fields beyond `name` and `type` | Optional fields |
| --- | --- | --- |
| `audit` | `sql`, `key`, `expect_rows` | — |
| `diff` | `before`, `after`, `key` | `after_key`, `value` |

An audit's key refers to the leftmost source column before any rename. A diff's key names a column of each saved output; use `after_key` for a renamed candidate column. Relation names accept DuckDB-style quoted identifiers and up to three parts. Key and value arguments for diff take the actual column name, with shell quoting for spaces. A diff checks multiplicities and optionally one column's values. Row order is irrelevant. Equal-count changes in other columns are outside the comparison.

## Machine-readable results

Consumers may rely on these fields in 1.x and should accept additional fields. Human-readable formatting and diagnostic wording can change.

- A trace includes `target`, `status`, `output_rows` and `stages`. Status is `present`, `removed` or `source_absent`. Each stage carries its clause and before/after counts. In a CTE trace, `cte` names the step or is null for the outer SELECT.
- An audit includes `source_rows`, `source_keys`, `report_rows`, `expected_rows`, `key`, `violation_count`, and `examples`. NULL source keys form one group.
- A diff includes `before_rows`, `after_rows`, `added_keys`, `removed_keys`, `increased_keys`, `decreased_keys`, `changed_keys` and `examples`. The four categories are disjoint. With `value`, `modified_keys` is a fifth category for equal key counts but different value multiplicities. Examples include key and row counts. Value examples show one decreased and one increased value where present, each with both frequencies. A null witness means no such value; a witness whose `value` is null describes SQL NULL. Text comparisons use exact UTF-8 bytes. Compatible numeric comparisons use DuckDB equality for the same type on both sides.
- Suite JSON has `schema_version: 1`, `failed_checks` and an ordered `checks` array. Each entry has `name`, `type`, `passed` and the corresponding `result`. The Markdown form includes check names and aggregate counts only. Source values can appear in text and JSON examples.

## Python

Audit keys, diff keys and selected diff values support booleans, fixed-width integers, DECIMAL, FLOAT/DOUBLE, VARCHAR, BLOB and UUID. A diff requires matching column types on both sides. Decimal values are rendered as exact strings, blobs as hex strings, and nonfinite floats as strings. For large JSON integers, use a decoder that preserves integer precision.

The checkout provides two offline adoption examples beyond the trace parser's SQL subset. The QuickBooks example compares a full pre-fix compilation with a local INNER-to-LEFT repair on synthetic staging data and finds the restored debit and credit for payment 1. The Shopify example compares the compiled query with a local shipping rollup and finds a value change for `save5`. Run `python -m pytest -q tests/test_diff.py tests/test_diff_values.py` after installing the test extra.

The public imports are `trace_row(connection, sql, key, value)`, `audit_rows(connection, sql, key, expected_rows, limit=5)`, `diff_rows(connection, before, after, key, *, after_key=None, value=None, limit=5)` and `check_project(path, *, limit=5)`. They return dictionaries. Callers of the first three own their DuckDB connection and transaction. When data can change concurrently, start a read-only transaction before obtaining counts and examples. The CLI and `check_project` handle the transaction for you.

Breaking changes to these contracts require a new major version. The supported SQL shapes and exhibited limitations are documented in the [README](../README.md#scope). A full-output diff runs on already-built tables or views, including outputs from SQL beyond the trace parser's scope.
