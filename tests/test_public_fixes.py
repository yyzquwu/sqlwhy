"""Verify that the public-fix report can be regenerated from its local evidence."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "research"


def test_ten_distinct_upstream_fixes():
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "cases").glob("*.json")]
    assert len(rows) == len({c["revision"] for c in rows}) == len({c["id"] for c in rows}) == 10
    for c in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", c["revision"])
        assert c["replay_kind"] in {"exact", "adapted", "reduced"}
        assert c["source_form"] in {"literal", "template", "generated", "fragment"}
        assert c["expected_before"] != c["expected_after"]
        if c["replay_kind"] == "exact":
            assert c["source_form"] == "literal"
            assert c["source_sql"].strip().rstrip(";") == c["before_sql"].strip().rstrip(";")


def test_public_fix_report_reproduces():
    result = subprocess.run([sys.executable, ROOT / "replay.py"], cwd=ROOT.parent, capture_output=True, check=True)
    assert result.stdout.decode("utf-8").splitlines() == (ROOT / "results.md").read_text(encoding="utf-8").splitlines()
