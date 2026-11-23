# Compiled dbt models in sqlwhy v0.4

Jaffle Shop Classic's `stg_orders` compiles to a linear CTE chain that sqlwhy traces and audits through the `id AS order_id` rename. Four pre-fix Fivetran models from the public-fix study also compile and run in DuckDB. Each hits a sqlwhy query-shape limitation.

## Replay offline

From the repo root, install `.[test]` in the project's environment once, then run the five offline checks. The replay uses DuckDB and the public `sqlwhy.trace_row` and `sqlwhy.audit_rows` APIs. It requires neither dbt nor external data.

```sh
python -m pytest -v tests/test_dbt_cte.py
```

### Jaffle Shop Classic

The unaltered `dbt compile` output and its original 99-row demo seed are checked in. The seed contains fictional orders with the same `INTEGER`, `INTEGER`, `DATE`, and `VARCHAR` columns created by `dbt seed`. The compiled relation name `"evidence"."main"."raw_orders"` resolves against a local database named `evidence.duckdb`. [Compiled SQL](compiled/stg_orders.sql) · [Pinned `stg_orders` source](https://github.com/dbt-labs/jaffle-shop-classic/blob/fd7bfacae4f497ff044a6a0275268676bf1b64c3/models/staging/stg_orders.sql) · [Pinned seed](https://github.com/dbt-labs/jaffle-shop-classic/blob/fd7bfacae4f497ff044a6a0275268676bf1b64c3/seeds/raw_orders.csv)

The derived fixture deliberately injects `WHERE status = 'completed'` into `renamed`. The audit expects one report row per `raw_orders.id`. Order 1 has status `returned`, so the trace shows the `renamed` filter reducing its row count from 1 to 0, with a `FALSE` predicate result. [Injected fixture](stg_orders_injected.sql)

| SQL | DuckDB total rows | DuckDB rows for `order_id = 1` | sqlwhy audit violations |
| --- | ---: | ---: | ---: |
| Original compilation | 99 | 1 | 0 |
| Deliberate filter mistake | 67 | 0 | 32 |

### Pre-fix Fivetran models

Each file is the full SQL emitted by `dbt compile` for DuckDB at the model's pre-fix revision. All four run in DuckDB 1.5.6 with synthetic relations matching the compiled catalog and schemas. This checks the compiled model SQL against controlled inputs. It leaves the upstream ingestion and staging pipelines out of the run. [Synthetic inputs](synthetic_staging.sql) · [Reduced-case study](../results.md)

| Case | DuckDB observed on synthetic inputs | First sqlwhy v0.4 diagnostic and offending clause |
| --- | --- | --- |
| a1 QuickBooks invoice join | `alpha` payment lines totaling 30 report 60. `beta` lines totaling 20 report 40. | `CTE 'invoice_link': Only one JOIN is supported`. The second `LEFT JOIN` brings in `invoice_pay`. `final` also groups and joins three inputs. [Compiled SQL](compiled/a1.sql) |
| a2 QuickBooks bill payment double entry | Payment 1 has no accounts-payable match and disappears. Payment 2 returns credit and debit rows. | `CTE 'bill_payment_join': SELECT does not support window functions`. The projection uses `ROW_NUMBER() OVER (…)`. `final` also uses `UNION ALL`. [Compiled SQL](compiled/a2.sql) |
| a3 Shopify checkout discounts | One `save5` discount worth 5 reports a total of 10 after two shipping-line matches. | `CTE 'abandoned_checkout': WHERE does not support the COALESCE function`. The predicate is `WHERE NOT COALESCE(is_deleted, FALSE)`. `abandoned_checkouts_aggregated` also uses two joins and `GROUP BY`. [Compiled SQL](compiled/a3.sql) |
| a5 Zendesk calendar resolution | The closed-only ticket remains in the final report with `NULL` resolution minutes. The solved ticket reports 15 minutes. | `CTE 'solved_times': SELECT GROUP clauses are unsupported`. The aggregate uses `GROUP BY 1, 2`. The final select also joins three inputs. [Compiled SQL](compiled/a5.sql) |

## Recompile against the upstream revisions

The optional check below needs Git and network access. It uses an isolated Python environment and dbt-core 1.10.20, dbt-duckdb 1.10.0, and DuckDB 1.5.6. Run it from the repo root. On POSIX, create the virtual environment under `${TMPDIR:-/tmp}` and use its `bin/python` to run the same pip and script commands.

```powershell
$dbtVenv = Join-Path $env:TEMP 'sqlwhy-dbt-1.10.20'
python -m venv $dbtVenv
$dbtPython = Join-Path $dbtVenv 'Scripts/python.exe'
& $dbtPython -m pip install dbt-core==1.10.20 dbt-duckdb==1.10.0 duckdb==1.5.6
& $dbtPython research/dbt/check_compilation.py
```

The script fetches each revision into a fresh temporary directory. It uses a `local_duckdb` profile with the `dev` target, `path: evidence.duckdb`, `schema: main`, and one thread. It runs `dbt seed --select raw_orders` for Jaffle Shop and `dbt deps` against the captured package locks for the Fivetran projects, then runs `dbt compile --select <model>` for each selected model. It checks the seed bytes and compares the generated SQL and package locks after normalizing platform line endings. The checked-in SQL retains the original Windows compile output, including whitespace.

| Case | Pinned pre-fix model source | Dependency selection |
| --- | --- | --- |
| a1 | [`int_quickbooks__invoice_join`](https://github.com/fivetran/dbt_quickbooks/blob/505a2f48c0f07f10d6ef0ef516b84e1a7800c48f/models/intermediate/int_quickbooks__invoice_join.sql) | Source package `0.10.1`. [Package lock](locks/a1.yml) |
| a2 | [`int_quickbooks__bill_payment_double_entry`](https://github.com/fivetran/dbt_quickbooks/blob/74da53b53cc1acf0cd29275744263dd4757ce589/models/double_entry_transactions/int_quickbooks__bill_payment_double_entry.sql) | Source package `0.10.1`. [Package lock](locks/a2.yml) |
| a3 | [`int_shopify__discounts__abandoned_checkouts`](https://github.com/fivetran/dbt_shopify/blob/304c7ee7f4c1d229981b491c9bf2b60a7411f73b/models/intermediate/int_shopify__discounts__abandoned_checkouts.sql) | The temporary manifest pins the upstream `feature/revamp/add-tax-line` branch to `dc5b7a64ccb979bdf55ac29e0c8ac488058cd376`. [Package lock](locks/a3.yml) |
| a5 | [`int_zendesk__ticket_resolution_times_calendar`](https://github.com/fivetran/dbt_zendesk/blob/1ad0a86859f8c589889e7a736130eadb04a4d6d2/models/resolution_times/int_zendesk__ticket_resolution_times_calendar.sql) | Source package `0.15.0`. [Package lock](locks/a5.yml) |

The model SQL and seed originate from dbt Labs' Jaffle Shop Classic and Fivetran's QuickBooks, Shopify, and Zendesk packages under the Apache License, Version 2.0. SQL generated by package macros uses the dependencies identified in the lock files. The pinned upstream projects carry no `NOTICE` file. The Jaffle Shop derivative is marked in the SQL itself. [Apache-2.0 license](../licenses/Apache-2.0.txt)
