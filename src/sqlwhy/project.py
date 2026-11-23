"""Run a versioned TOML suite on one read-only DuckDB snapshot."""
import tomllib
from pathlib import Path

import duckdb

from .audit import audit_rows
from .diff import diff_rows
from .files import TABLE_NAME, open_db, open_tables


def _fields(data, required, optional=()):
    if not isinstance(data, dict):
        raise ValueError("Expected a TOML table.")
    missing = sorted(set(required) - data.keys())
    extra = sorted(data.keys() - set(required) - set(optional))
    if missing:
        raise ValueError("Missing field(s): " + ", ".join(missing))
    if extra:
        raise ValueError("Unknown field(s): " + ", ".join(extra))


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string.")
    return value


def _load_checks(items, root):
    if not isinstance(items, list) or not items:
        raise ValueError("Define at least one [[checks]] table.")
    checks, names = [], set()
    for index, row in enumerate(items, 1):
        label = f"Check {index}"
        try:
            if not isinstance(row, dict):
                raise ValueError("Expected a [[checks]] table.")
            name = _text(row.get("name"), "name")
            label = f"Check {name!r}"
            if name in names:
                raise ValueError("Duplicate check name.")
            names.add(name)
            kind = row.get("type")
            common = ("name", "type", "key")
            if kind == "audit":
                _fields(row, (*common, "sql", "expect_rows"))
                n = row["expect_rows"]
                if type(n) is not int or n < 0:
                    raise ValueError("expect_rows must be a nonnegative integer.")
                sql = (root / _text(row["sql"], "sql")).read_text(encoding="utf-8-sig")
                args = dict(sql=sql, key=_text(row["key"], "key"), expected_rows=n)
            elif kind == "diff":
                _fields(row, (*common, "before", "after"), ("after_key", "value"))
                args = {k: _text(v, k) for k, v in row.items() if k not in ("name", "type")}
            else:
                raise ValueError("type must be 'audit' or 'diff'.")
            checks.append((name, kind, args))
        except (ValueError, OSError) as err:
            raise ValueError(f"{label}: {err}") from err
    return checks


def check_project(path, *, limit=5) -> dict:
    """Execute all checks. Return complete results or raise on configuration/execution errors."""
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer.")
    path = Path(path)
    root = path.parent
    config = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    _fields(config, ("version", "checks"), ("database", "tables"))
    if type(config["version"]) is not int or config["version"] != 1:
        raise ValueError("Expected project version = 1.")
    if ("database" in config) == ("tables" in config):
        raise ValueError("Choose one database file or one [tables] section.")
    checks = _load_checks(config["checks"], root)
    if "database" in config:
        con = open_db(root / _text(config["database"], "database"))
    else:
        tables = config["tables"]
        if not isinstance(tables, dict) or not tables:
            raise ValueError("[tables] must map table names to local CSV or Parquet files.")
        if any(not TABLE_NAME.fullmatch(k) for k in tables):
            raise ValueError("[tables] names must be simple SQL identifiers.")
        con = open_tables([f"{k}={root / _text(v, k)}" for k, v in tables.items()])
    records = []
    with con:
        con.execute("BEGIN TRANSACTION READ ONLY")
        for name, kind, args in checks:
            try:
                if kind == "audit":
                    result = audit_rows(con, **args, limit=limit)
                    fails = result["violation_count"]
                else:
                    result = diff_rows(con, **args, limit=limit)
                    fails = result["changed_keys"]
            except (ValueError, duckdb.Error) as err:
                raise ValueError(f"Check {name!r}: {err}") from err
            records.append(dict(name=name, type=kind, passed=fails == 0, result=result))
        con.commit()
    return dict(schema_version=1, failed_checks=sum(not r["passed"] for r in records), checks=records)
