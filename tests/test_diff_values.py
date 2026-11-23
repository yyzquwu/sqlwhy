from collections import Counter
from pathlib import Path
from random import Random

import duckdb
import pytest

from sqlwhy import diff_rows


def test_value_diff_tracks_duplicate_and_null_values_in_any_order():
    rng = Random(108)
    with duckdb.connect() as con:
        for _ in range(20):
            old = [(rng.choice([None, 1, 2, 3]), rng.choice([None, 4, 5])) for _ in range(12)]
            new = old.copy()
            rng.shuffle(new)
            if rng.random() < 0.8:
                new[:3] = [(None, 4), (1, None), (7, 6)]
            for name, rows in [('prev', old), ('curr', new)]:
                con.execute(f'CREATE OR REPLACE TABLE {name}(id INT, amt INT)')
                con.executemany(f'INSERT INTO {name} VALUES (?, ?)', rows)
            r = diff_rows(con, 'prev', 'curr', 'id', value='amt', limit=10)
            changed = modified = 0
            for key in {row[0] for row in old + new}:
                b = Counter(v for k, v in old if k == key)
                a = Counter(v for k, v in new if k == key)
                if a == b:
                    continue
                changed += 1
                modified += a.total() == b.total()
                example, = [e for e in r['examples'] if e['key'] == key]
                for field, delta in [('decreased_value', b - a), ('increased_value', a - b)]:
                    record = example[field]
                    if delta:
                        v = min(delta, key=lambda x: (x is not None, x))
                        assert record == {'value': v, 'before_rows': b[v], 'after_rows': a[v]}
                    else:
                        assert record is None
            assert (r['changed_keys'], r['modified_keys'], len(r['examples'])) == (changed, modified, changed)


def test_shopify_discount_changes_while_row_count_stays_flat(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'research/dbt'
    sql = (fixture / 'compiled/a3.sql').read_text(encoding='utf-8')
    source = '"evidence"."main_stg_shopify"."stg_shopify__abandoned_checkout_shipping_line"'
    assert sql.count(source) == 1
    rollup = f'(SELECT checkout_id, source_relation, sum(price) AS price FROM {source} GROUP BY 1, 2)'
    with duckdb.connect(str(tmp_path / 'evidence.duckdb')) as con:
        con.execute((fixture / 'synthetic_staging.sql').read_text(encoding='utf-8'))
        con.execute(f'CREATE TABLE old AS {sql}')
        con.execute(f'CREATE TABLE new AS {sql.replace(source, rollup)}')
        assert diff_rows(con, 'old', 'new', 'code')['changed_keys'] == 0
        r = diff_rows(con, 'old', 'new', 'code', value='total_abandoned_checkout_discount_amount')
        assert (r['before_rows'], r['after_rows'], r['changed_keys'], r['modified_keys']) == (1, 1, 1, 1)
        ex, = r['examples']
        assert (ex['key'], ex['decreased_value']['value'], ex['increased_value']['value']) == ('save5', 10, 5)
        with pytest.raises(ValueError, match='Unknown value'):
            diff_rows(con, 'old', 'new', 'code', value='absent')
