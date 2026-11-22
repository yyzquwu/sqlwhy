import json

import pytest

from sqlwhy.cli import main
from sqlwhy.demo import DEMOS, open_demo
from sqlwhy.engine import trace_row


def test_flat_counts_shows_equal_totals_and_opposing_changes(capsys):
    assert main(["demo", "flat-counts"]) == 0
    out = capsys.readouterr().out
    assert "Source rows: 2  |  Report rows: 2" in out
    doubled, dropped = out.split("Order 42")
    assert "Order 41" in doubled and "LEFT JOIN" in doubled and "[1 -> 2]" in doubled
    assert "WHERE p.status = 'settled'  [1 -> 0]" in dropped
    assert "UNKNOWN (SQL NULL)" in dropped
    assert len(out.splitlines()) <= 20


def test_flat_counts_json_keeps_full_trace_evidence(capsys):
    assert main(["demo", "flat-counts", "--json"]) == 0
    out = capsys.readouterr()
    result = json.loads(out.out)
    assert not out.err
    sql = DEMOS["flat-counts"][1]
    with open_demo("flat-counts") as con:
        assert con.execute(sql).fetchall() == [(41,), (41,)]
        traces = [trace_row(con, sql, "o.id", key) for key in (41, 42)]
    assert result == {"source_rows": 2, "result_rows": 2, "traces": traces}
    assert traces[1]["stages"][-1]["truth_counts"]["UNKNOWN"] == 1


@pytest.mark.parametrize("args", [["--expect", "present"], ["--expect-rows", "0"]])
def test_flat_counts_rejects_single_trace_expectations(args, capsys):
    with pytest.raises(SystemExit) as err:
        main(["demo", "flat-counts", "--json", *args])
    assert err.value.code == 2
    out = capsys.readouterr()
    assert not out.out
    assert "flat-counts traces two orders" in out.err
