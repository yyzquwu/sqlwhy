# Grain audit benchmark

From the checkout root, install SQLWhy and run the benchmark in the same Python environment.

```sh
python -m pip install -e .
python benchmarks/audit.py
```

The benchmark creates 100,000 distinct order keys and 100,000 payment rows in memory. Orders with `id % 100 = 0` have two settled payments, and orders with `id % 100 = 1` have none. A `LEFT JOIN` followed by `WHERE p.status = 'settled'` returns exactly 100,000 report rows. With one expected row per key, 1,000 keys have two rows and 1,000 have zero.

Both modes use one DuckDB thread and one warmup per mode on the same connection. The script measures seven calls to `audit_rows` with `time.perf_counter`, including SQL parsing and execution and, at limit 5, five detailed traces. It times data setup separately and checks the original SELECT with a DuckDB `GROUP BY` before timing the audit. Assertions after each call verify the counts and, when requested, the returned keys and representative trace stages.

## Measured run

The local run used Windows 11 build 26200, an Intel Core Ultra 7 258V with 8 cores and 8 logical processors, and 31.6 GiB of RAM reported by Windows. It ran with Python 3.12.14, DuckDB 1.5.6, and SQLGlot 30.33.0.

```powershell
.venv/Scripts/python.exe benchmarks/audit.py
```

Fixture setup took 8.2 ms. Timings vary with hardware and system load.

| Mode | Median | Range across seven runs |
| --- | ---: | ---: |
| `limit=0` (counts only) | 22.5 ms | 21.8–24.4 ms |
| `limit=5` (counts and five traces) | 58.5 ms | 56.1–60.0 ms |
