"""Replay the checked-in public-fix study with synthetic tables and no network."""

import json
from pathlib import Path

import duckdb

from sqlwhy import trace_row
from sqlwhy.query import parse_query

ROOT = Path(__file__).resolve().parent


def cases():
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "cases").glob("*.json"))]


def native_count(con, sql, column, value):
    column = '"' + column.replace('"', '""') + '"'
    sql = sql.strip().removesuffix(";")
    return con.execute(f"SELECT count(*) FROM ({sql}\n) r WHERE r.{column} = ?", [value]).fetchone()[0]


def original(case, con):
    if case["source_form"] != "literal":
        if case.get("template_engine") == "dbt":
            return "raw dbt template rejected (compiled SQL untested)"
        return f'{case["source_form"]} (no standalone query tested)'
    sql = case["source_sql"]
    column = case.get("original_output_key")
    if column:
        count = native_count(con, sql, column, case["value"])
        if count != case["expected_before"]:
            raise ValueError(f'{case["id"]}: original fixture differs from expected count')
    try:
        parse_query(sql)
    except ValueError as err:
        name = case.get("source_label", "Query")
        return f"{name} ran in DuckDB, trace rejected: {err}" if column else f"Parse rejected: {err}"
    if not column:
        return "syntax accepted (parse only)"
    trace = trace_row(con, sql, case.get("original_key", case["key"]), case["value"])
    if trace["output_rows"] != count:
        raise ValueError(f'{case["id"]}: original trace differs from native original')
    return f"ran unchanged ({stage_evidence(trace)})"


def replay(case):
    """Compare trace counts with a native execution of each complete local query."""
    with duckdb.connect() as con:
        con.execute(case["setup_sql"])
        source_result = original(case, con)
        counts, traces = {}, {}
        for side in ("before", "after"):
            sql = case[f"{side}_sql"]
            count = native_count(con, sql, "id", case["value"])
            if count != case[f"expected_{side}"]:
                raise ValueError(f'{case["id"]} {side}: expected {case[f"expected_{side}"]} rows, got {count}')
            counts[side] = count
            try:
                traces[side] = trace_row(con, sql, case["key"], case["value"])
            except ValueError as err:
                traces[side] = str(err)
            else:
                if traces[side]["output_rows"] != count:
                    raise ValueError(f'{case["id"]} {side}: trace disagrees with DuckDB')
    stage = case["expected_stage"]
    if stage:
        trace = traces["before"]
        changed = [] if isinstance(trace, str) else [
            s["stage"].upper() for s in trace["stages"][1:] if s["before"] != s["after"]
        ]
        if stage.upper() not in changed:
            raise ValueError(f'{case["id"]}: expected a row count change at {stage}, observed {changed}')
    return source_result, counts, traces


def stage_evidence(trace):
    if isinstance(trace, str):
        return f"Unsupported: {trace}"
    return ", ".join(
        f'{s["stage"].upper()} {s["before"]} → {s["after"]}'
        for s in trace["stages"][1:] if s["before"] != s["after"]
    ) or "No stage changed the key count"


def report():
    lines = [
        "# Ten public SQL fixes",
        "",
        "Replay with `python research/replay.py` from a checkout with sqlwhy installed. "
        "Each source link identifies an upstream fix. The two row counts come from executing "
        "the local before and after queries directly in DuckDB on synthetic data. "
        "The trace columns show a separate run of sqlwhy on each local query.",
        "",
        "| Fix | Original pre-fix source | Local replay | Rows for key, before → fixed | Before trace | Fixed trace |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    notes = []
    for case in cases():
        source_result, counts, traces = replay(case)
        cols = (
            f'[{case["id"]}]({case["upstream"]}) {case["title"]}', source_result, case["replay_kind"],
            f'{counts["before"]} → {counts["after"]}', stage_evidence(traces["before"]), stage_evidence(traces["after"]),
        )
        lines.append("| " + " | ".join(s.replace("|", "\\|").replace("\n", " ") for s in cols) + " |")
        reporter = f' · [Reporter’s query]({case["report_url"]})' if case.get("report_url") else ""
        notes.append(
            f'- **{case["id"]}** {case["evidence"]} {case["adaptation"]} '
            f'[Source]({case["source_url"]}){reporter} · [Fixture](cases/{case["id"]}.json)'
        )
    attribution = (
        "The four Fivetran model excerpts use the [Apache 2.0 license](licenses/Apache-2.0.txt). "
        "The MIT Open Learning case includes its upstream BSD 3-Clause notice in the fixture."
    )
    return "\n".join([*lines, "", "## Case notes", "", *notes, "", attribution]) + "\n"


def check_templates():
    """Fetch pinned pre-fix models and verify the quoted clause and raw parser result."""
    from urllib.request import urlopen

    for c in cases():
        if c.get("template_engine") != "dbt":
            continue
        url = c["source_before_url"].replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
        with urlopen(url, timeout=20) as response:
            sql = response.read().decode("utf-8")
        if c["source_sql"] not in sql:
            raise ValueError(f'{c["id"]}: excerpt differs from the pinned file')
        try:
            parse_query(sql)
        except ValueError as err:
            print(f'{c["id"]}: {err}')
        else:
            raise ValueError(f'{c["id"]}: raw template was accepted; revisit the report')


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-templates", action="store_true", help="verify raw dbt templates on GitHub")
    if parser.parse_args().check_templates:
        check_templates()
    else:
        sys.stdout.buffer.write(report().encode("utf-8"))
