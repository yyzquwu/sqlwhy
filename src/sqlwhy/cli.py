import argparse
import json
import re
import sys
from pathlib import Path

import duckdb

from .audit import audit_rows
from .demo import DEMOS, open_demo, trace_flat_counts
from .engine import trace_row
from .files import SAFE_CONFIG, open_tables

STATUSES = ("present", "removed", "source_absent")


def _trace_arg(arg):
    key, sep, raw = arg.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError("expected KEY=VALUE, such as orders.id=42")
    if raw.startswith('"'):
        try:
            val = json.loads(raw)
        except json.JSONDecodeError as err:
            raise argparse.ArgumentTypeError("invalid JSON-quoted trace value") from err
    elif raw.upper() == "NULL":
        val = None
    else:
        val = int(raw) if re.fullmatch(r"0|-?[1-9][0-9]*", raw) else raw
    return key.strip(), val


def _nonnegative_int(raw):
    try:
        count = int(raw)
    except ValueError:
        count = -1
    if count < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return count


def _parser(command):
    demo, audit = command == "demo", command == "audit"
    names = "\n".join(f"  {name:<16} {description}" for name, (description, _) in DEMOS.items())
    examples = (
        "Examples\n"
        "  sqlwhy report.sql --db audit.duckdb --trace orders.id=42\n"
        "  sqlwhy report.sql --table orders=orders.csv --trace orders.id=42\n"
        "  sqlwhy audit report.sql --db audit.duckdb --key orders.id --expect-rows 1\n"
        "  sqlwhy demo flat-counts --json\n\n"
    )
    parser = argparse.ArgumentParser(
        prog=f"sqlwhy {command}" if command else "sqlwhy",
        description=("Check the report row count for every source key."
                     if audit else "Trace a source row through the joins and WHERE clause of a SQL query."),
        epilog=None if audit else ("" if demo else examples) + "Built-in demos\n" + names,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if demo:
        parser.add_argument("name", nargs="?", choices=DEMOS, default="left-join-null")
    else:
        parser.add_argument("sql_file", metavar="QUERY.sql", type=Path, help="UTF-8 SQL file")
        inputs = parser.add_mutually_exclusive_group(required=True)
        inputs.add_argument("--db", type=Path, help="existing DuckDB file opened read-only")
        inputs.add_argument("--table", action="append", metavar="NAME=FILE",
                            help="local CSV or Parquet file to load as NAME (repeatable)")
        if audit:
            parser.add_argument("--key", required=True, metavar="COLUMN", help="key column on the leftmost table")
            parser.add_argument("--limit", metavar="N", type=_nonnegative_int, default=5,
                                help="maximum number of violation examples (default: 5)")
        else:
            parser.add_argument("--trace", required=True, metavar="KEY=VALUE", type=_trace_arg,
                                help="source key and value (NULL for SQL NULL, double quotes for text)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    if not audit:
        parser.add_argument("--expect", choices=STATUSES, help="exit 1 when the trace status differs")
    parser.add_argument("--expect-rows", metavar="N", type=_nonnegative_int, required=audit,
                        help="exit 1 when any source key has a different report row count"
                        if audit else "exit 1 when the trace output row count differs")
    return parser


def _open_db(path):
    if not path.is_file():
        raise ValueError(f"database file does not exist or is not a file: {path}")
    return duckdb.connect(str(path.resolve()), read_only=True, config=SAFE_CONFIG)


def _literal(value):
    return "NULL" if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _show_stage(stage):
    count = str(stage["after"])
    if stage["before"] is not None:
        count = f"{stage['before']} -> {count}"
    print(f"  {stage['clause']}  [{count}]")
    print(f"    {stage['detail']}")
    for sample in stage.get("samples", ()):
        inputs = ", ".join(f"{key} = {_literal(val)}" for key, val in sample["inputs"].items())
        if inputs:
            print(f"    {sample['outcome']} with {inputs}")


def _show(result):
    target = result["target"]
    print(f"Tracing {target['key']} = {_literal(target['value'])}")
    for stage in result["stages"]:
        _show_stage(stage)
    print(f"Result  {result['status']}")


def _show_flat_counts(result):
    print(f"Source rows: {result['source_rows']}  |  Report rows: {result['result_rows']}")
    for trace in result["traces"]:
        print(f"\nOrder {trace['target']['value']}")
        for stage in trace["stages"][1:]:
            if stage["before"] != stage["after"]:
                _show_stage(stage)


def _show_audit(result):
    expected = result["expected_rows"]
    examples = result["examples"]
    rows = "row" if expected == 1 else "rows"
    print(f"Expected {expected} report {rows} per {result['key']}")
    print(f"Source rows {result['source_rows']}  |  Source keys {result['source_keys']}"
          f"  |  Report rows {result['report_rows']}")
    print(f"Violations {result['violation_count']}  |  Examples shown {len(examples)}")
    for trace in examples:
        target = trace["target"]
        source, *stages = trace["stages"]
        print(f"\n{target['key']} = {_literal(target['value'])}  [expected {expected}, got {trace['output_rows']}]")
        if source["after"] != expected:
            print(f"  {source['clause']}  [source count already differs: {source['after']}]")
        for stage in stages:
            if stage["before"] == stage["after"]:
                continue
            detail = ""
            if truth := stage.get("truth_counts"):
                detail = (f"  {truth['UNCLASSIFIED']} unclassified" if "UNCLASSIFIED" in truth else
                          f"  FALSE {truth['FALSE']}, UNKNOWN {truth['UNKNOWN']} (SQL NULL)")
            print(f"  {stage['clause']}  [{stage['before']} -> {stage['after']}]{detail}")


def main(argv=None):
    if argv is None:
        for stream in (sys.stdout, sys.stderr):
            if getattr(stream, "reconfigure", None):
                stream.reconfigure(errors="backslashreplace")
        argv = sys.argv[1:]
    command = argv[0] if argv and argv[0] in ("demo", "audit") else None
    demo, audit = command == "demo", command == "audit"
    parser = _parser(command)
    args = parser.parse_args(argv[1:] if command else argv)
    flat = demo and args.name == "flat-counts"
    if flat and (args.expect is not None or args.expect_rows is not None):
        parser.error("flat-counts traces two orders. Use --expect and --expect-rows with a single trace.")
    try:
        if demo:
            sql = DEMOS[args.name][1]
            con = open_demo(args.name)
        else:
            sql = args.sql_file.read_text(encoding="utf-8-sig")
            con = _open_db(args.db) if args.db else open_tables(args.table)
        with con:
            if audit:
                con.execute("BEGIN TRANSACTION READ ONLY")
                result = audit_rows(con, sql, args.key, args.expect_rows, limit=args.limit)
                con.commit()
            elif flat:
                result = trace_flat_counts(con)
            else:
                key, value = ("o.id", 42) if demo else args.trace
                result = trace_row(con, sql, key, value)
    except (OSError, ValueError, duckdb.Error) as err:
        parser.error(str(err))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif audit:
        _show_audit(result)
    elif flat:
        _show_flat_counts(result)
    else:
        _show(result)
    if audit:
        return int(result["violation_count"] > 0)
    code = 0
    if args.expect and args.expect != result["status"]:
        print(f"Expected {args.expect}, got {result['status']}.", file=sys.stderr)
        code = 1
    if args.expect_rows is not None and args.expect_rows != result["output_rows"]:
        print(f"Expected row count {args.expect_rows}, got {result['output_rows']}.", file=sys.stderr)
        code = 1
    return code
