import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from sqlwhy import check_project


def test_checkout_suite_from_unrelated_directory(tmp_path):
    config = Path(__file__).resolve().parents[1] / 'examples/audit/sqlwhy.toml'
    run = subprocess.run([sys.executable, '-m', 'sqlwhy', 'check', str(config), '--json'],
                         cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0 and not run.stderr
    report = json.loads(run.stdout)
    assert report == check_project(config)
    assert (report['schema_version'], report['failed_checks']) == (1, 0)
    assert [r['type'] for r in report['checks']] == ['audit', 'diff']


def test_failing_suite_runs_later_checks_and_counts_all_keys(tmp_path):
    db = tmp_path / 'reviews.duckdb'
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE old AS SELECT 1 id, 4 amt")
        con.execute("CREATE TABLE new AS SELECT 1 id, 9 amt")
    snapshot = db.read_bytes()
    (tmp_path / 'report.sql').write_text("SELECT id FROM old WHERE id > 1", encoding='utf-8')
    cfg = tmp_path / 'suite.toml'
    cfg.write_text('''version = 1
database = "reviews.duckdb"
[[checks]]
type = "audit"
name = "count"
sql = "report.sql"
key = "id"
expect_rows = 1
[[checks]]
type = "diff"
name = "metric"
before = "old"
after = "new"
key = "id"
value = "amt"
''', encoding='utf-8')
    report = check_project(cfg, limit=0)
    assert report['failed_checks'] == 2
    assert report['checks'][1]['result']['modified_keys'] == 1
    assert all(not row['result']['examples'] for row in report['checks'])
    assert db.read_bytes() == snapshot
    args = [sys.executable, '-m', 'sqlwhy', 'check', str(cfg), '--json']
    p = subprocess.run(args, capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)['failed_checks'] == 2
    cfg.write_text(cfg.read_text().replace('value = "amt"', 'value = "actuall"'))
    p = subprocess.run(args, capture_output=True, text=True)
    assert p.returncode == 2 and not p.stdout and "Check 'metric': Unknown value" in p.stderr


@pytest.mark.parametrize('snippet, error', [
    ('version = true\nchecks = []', 'version = 1'),
    ('version = 1\nchecks = []\nfoo = 1', 'Unknown field'),
    ('version = 1\ndatabase = "x"\nchecks = []\n[tables]', 'Choose one database'),
    ('version = 1\ndatabase = "x"\nchecks = [{name="typo", type="dif"}]', "Check 'typo': type"),
])
def test_invalid_suite_fails_clearly(tmp_path, snippet, error):
    path = tmp_path / 'sqlwhy.toml'
    path.write_text(snippet, encoding='utf-8')
    with pytest.raises(ValueError, match=error):
        check_project(path)
