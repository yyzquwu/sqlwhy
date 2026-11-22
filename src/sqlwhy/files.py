"""Open explicitly named local files as tables in an ephemeral DuckDB database."""
import re
from pathlib import Path

import duckdb

SAFE_CONFIG = {
    "enable_external_access": "false",
    "allow_unsigned_extensions": "false",
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
    "temp_directory": "",
}


def open_tables(specs):
    """Materialize named CSV/Parquet files, then disable external access for tracing."""
    tables = {}
    for spec in specs:
        name, sep, filename = spec.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) or not filename:
            raise ValueError("--table expects TABLE_NAME=path/to/file.csv or .parquet")
        if name.lower() in tables:
            raise ValueError(f"Duplicate table name: {name}")
        path = Path(filename).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in {".csv", ".parquet"}:
            raise ValueError(f"Expected a local .csv or .parquet file: {path}")
        if any(char in str(path) for char in "*?["):
            raise ValueError("DuckDB glob characters in file paths are unsupported")
        tables[name.lower()] = path
    if not tables:
        raise ValueError("Supply at least one named table")
    con = duckdb.connect(config={k: v for k, v in SAFE_CONFIG.items() if k != "enable_external_access"})
    try:
        for name, path in tables.items():
            reader = "read_csv_auto" if path.suffix.lower() == ".csv" else "read_parquet"
            con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM {reader}(?)', [str(path)])
        con.execute("SET enable_external_access = false")
        return con
    except BaseException:
        con.close()
        raise
