import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import duckdb
import pytest

from sqlwhy import diff_rows


@pytest.mark.parametrize("limit", [0, 2, 20])
def test_counts_match_independent_oracle(limit):
    before = [None, 1, 1, 1, 2, 3, 4]
    after = [None, None, 1, 2, 3, 3, 5]
    b, a = Counter(before), Counter(after)
    with duckdb.connect() as con:
        for name, key, rows in [('previous', 'id', before), ('current', 'order id', after)]:
            con.execute(f'CREATE TABLE {name} ("{key}" INT)')
            con.executemany(f'INSERT INTO {name} VALUES (?)', [(x,) for x in rows])
        r = diff_rows(con, 'previous', 'current', 'id', after_key='order id', limit=limit)
        assert (r['before_rows'], r['after_rows'], r['changed_keys']) == (7, 7, 5)
        assert [r[k] for k in ('added_keys', 'removed_keys', 'increased_keys', 'decreased_keys')] == [1, 1, 2, 1]
        keys = [k for k in (None, 1, 2, 3, 4, 5) if b[k] != a[k]][:limit]
        assert r['examples'] == [dict(key=k, before_rows=b[k], after_rows=a[k]) for k in keys]


def test_empty_collated_text_quoted_tables_and_type_mismatch():
    with duckdb.connect() as con:
        con.execute('CREATE SCHEMA qa')
        con.execute('CREATE TABLE qa."before.run"(id VARCHAR COLLATE NOCASE)')
        con.execute('CREATE TABLE "__sqlwhy_diff"(id VARCHAR)')
        def run():
            return diff_rows(con, 'qa."before.run"', '__sqlwhy_diff', 'id')
        assert run()['changed_keys'] == 0
        con.execute("INSERT INTO qa.\"before.run\" VALUES ('ABC'), ('Abc')")
        con.execute("INSERT INTO __sqlwhy_diff VALUES ('ABC'), ('abc')")
        assert [s['key'] for s in run()['examples']] == ['Abc', 'abc']
        con.execute('CREATE OR REPLACE TABLE __sqlwhy_diff AS SELECT 1 AS id')
        with pytest.raises(ValueError, match="types must match"):
            run()


def test_diff_console_example_and_error():
    root = Path(__file__).resolve().parents[1] / 'examples'
    args = [sys.executable, '-m', 'sqlwhy', 'diff', 'old', 'new',
            '--table', 'old=orders.csv', '--table', 'new=payments_fanout.csv',
            '--key', 'id', '--after-key', 'order_id', '--json']
    p = subprocess.run(args, cwd=root, capture_output=True, text=True)
    assert p.returncode == 1 and not p.stderr
    assert json.loads(p.stdout)['changed_keys'] == 2
    args[args.index('order_id')] = 'absent'
    p = subprocess.run(args, cwd=root, capture_output=True, text=True)
    assert p.returncode == 2 and not p.stdout and "Unknown key" in p.stderr


def test_materialized_quickbooks_payment_repair(tmp_path):
    fixtures = Path(__file__).resolve().parents[1] / 'research/dbt'
    sql = (fixtures / 'compiled/a2.sql').read_text(encoding='utf-8')
    assert sql.count('inner join ap_accounts') == 1
    repaired = sql.replace('inner join ap_accounts', 'left join ap_accounts')
    with duckdb.connect(str(tmp_path / 'evidence.duckdb')) as con:
        con.execute((fixtures / 'synthetic_staging.sql').read_text(encoding='utf-8'))
        con.execute(f'CREATE TABLE before_fix AS {sql}')
        con.execute(f'CREATE TABLE after_fix AS {repaired}')
        r = diff_rows(con, 'before_fix', 'after_fix', 'transaction_id')
        assert (r['before_rows'], r['after_rows'], r['changed_keys']) == (2, 4, 1)
        assert r['examples'] == [{'key': 1, 'before_rows': 0, 'after_rows': 2}]
