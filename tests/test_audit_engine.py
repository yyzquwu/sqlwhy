import duckdb
import pytest

import sqlwhy.audit as audit
from sqlwhy import audit_rows, trace_row


@pytest.fixture
def con():
    with duckdb.connect() as connection:
        yield connection


def test_quoted_key_aliases_and_repeated_direct_projection(con):
    con.execute('CREATE TABLE "items"("id.name" INTEGER); INSERT INTO items VALUES (2), (1), (1)')
    sql = '-- report\nSELECT i."id.name" AS "Display Key", i.* FROM items i; -- trailing comment'
    result = audit_rows(con, sql, '"i"."id.name"', 1)
    assert (result["source_rows"], result["source_keys"], result["report_rows"], result["violation_count"]) == (3, 2, 3, 1)
    assert result["examples"] == [trace_row(con, sql, '"i"."id.name"', 1)]


@pytest.mark.parametrize(("projection", "message"), [
    ('l.id + 0 AS id', 'projected directly'),
    ('r.id AS id', 'projected directly'),
    ('r.*', 'projected directly'),
    ('l.id, r.id', 'ambiguous'),
    ('l.id AS "Key", r.id AS "key"', 'ambiguous'),
    ('*', 'ambiguous'),
])
def test_key_requires_a_direct_unambiguous_output(con, projection, message):
    con.execute('CREATE TABLE l(id INTEGER); CREATE TABLE r(id INTEGER)')
    with pytest.raises(ValueError, match=message):
        audit_rows(con, f'SELECT {projection} FROM l JOIN r ON l.id = r.id', 'l.id', 1)


def test_relation_names_and_projected_names_do_not_shadow_internal_bindings(con):
    con.execute('CREATE TABLE __sqlwhy_report(c0 INTEGER); INSERT INTO __sqlwhy_report VALUES (2), (3)')
    con.execute('CREATE TABLE __sqlwhy_counts(c0 INTEGER); INSERT INTO __sqlwhy_counts VALUES (2), (2)')
    sql = ('SELECT r.c0 AS c1, p.c0 AS c0 FROM __sqlwhy_report r '
           'JOIN __sqlwhy_counts p ON r.c0 = p.c0')
    data = audit_rows(con, sql, 'r.c0', 1)
    assert (data['source_rows'], data['report_rows'], data['violation_count']) == (2, 2, 2)
    assert [(ex['target']['value'], ex['output_rows']) for ex in data['examples']] == [(2, 2), (3, 0)]


def test_collated_key_groups_and_uses_deterministic_original_representatives(con):
    con.execute("CREATE TABLE items(k VARCHAR COLLATE NOCASE, n INT)")
    con.execute("INSERT INTO items VALUES ('a', 1), ('A', 1), ('b', 1), ('B', 2), (NULL, 2)")
    sql = "SELECT k FROM items WHERE n = 1"
    result = audit_rows(con, sql, "k", 2)
    assert (result['source_keys'], result['report_rows'], result['violation_count']) == (3, 3, 2)
    assert result['examples'] == [trace_row(con, sql, 'k', value) for value in (None, 'B')]


def test_untyped_null_view_key_matches_native_counts(con):
    con.execute("CREATE VIEW items AS SELECT NULL AS id")
    sql = "SELECT id FROM items"
    result = audit_rows(con, sql, "id", 0)
    assert (result['source_rows'], result['source_keys'], result['report_rows'], result['violation_count']) == (1, 1, 1, 1)
    assert result['examples'] == [trace_row(con, sql, 'id', None)]


@pytest.mark.parametrize('key_type', ['INTEGER[]', 'TIMESTAMP_NS', 'UNION(s VARCHAR, i INTEGER)'])
def test_unsupported_key_types_are_rejected_for_empty_report_and_zero_limit(con, key_type):
    con.execute(f'CREATE TABLE items(id {key_type})')
    with pytest.raises(ValueError, match='does not support key type'):
        audit_rows(con, 'SELECT id FROM items WHERE FALSE', 'id', 1, limit=0)


def test_struct_field_disguised_as_qualified_key_is_rejected(con):
    con.execute("CREATE TABLE items AS SELECT {'k': 9} AS a")
    with pytest.raises(ValueError, match='key column exposed by the source table'):
        audit_rows(con, 'SELECT a.k FROM items a', 'a.k', 1)


def test_unsafe_original_projection_is_evaluated_in_totals_only_mode(con):
    con.execute("CREATE TABLE items(id INTEGER, payload VARCHAR); INSERT INTO items VALUES (1, 'bad')")
    with pytest.raises(duckdb.ConversionException):
        audit_rows(con, 'SELECT id, CAST(payload AS INTEGER) FROM items', 'id', 1, limit=0)


def test_guarded_filter_preserves_native_evaluation_and_trace_evidence(con):
    con.execute("CREATE TABLE items(id INTEGER, payload VARCHAR); INSERT INTO items VALUES (1, 'bad'), (2, '2')")
    sql = "SELECT id, CAST(payload AS INTEGER) FROM items WHERE payload <> 'bad' AND CAST(payload AS INTEGER) > 0"
    result = audit_rows(con, sql, 'id', 1)
    removed, = result['examples']
    assert (result['report_rows'], result['violation_count']) == (1, 1)
    assert removed == trace_row(con, sql, 'id', 1)
    assert removed['stages'][-1]['truth_counts'] == {'TRUE': 0, 'FALSE': None, 'UNKNOWN': None, 'UNCLASSIFIED': 1}


def test_count_every_violation_but_only_trace_limited_examples(con, monkeypatch):
    con.execute('CREATE TABLE items AS SELECT i::INTEGER AS id FROM range(10000) AS r(i)')
    seen = []
    trace = audit._trace

    def record(connection, plan, key, key_col, value, prepared):
        seen.append(value)
        return trace(connection, plan, key, key_col, value, prepared)

    monkeypatch.setattr(audit, '_trace', record)
    sql = 'SELECT id FROM items WHERE FALSE'
    result = audit_rows(con, sql, 'id', 1, limit=2)
    assert result['violation_count'] == 10000
    assert seen == [0, 1]
    assert audit_rows(con, sql, 'id', 1, limit=0) == {**result, 'examples': []}
    assert seen == [0, 1]


def test_zero_expected_empty_input_and_large_nonnegative_arguments(con):
    con.execute('CREATE TABLE items(id INTEGER)')
    sql = 'SELECT id FROM items'
    result = audit_rows(con, sql, 'id', 0)
    assert result == {'key': 'id', 'expected_rows': 0, 'source_rows': 0, 'source_keys': 0,
                      'report_rows': 0, 'violation_count': 0, 'examples': []}
    con.execute('INSERT INTO items VALUES (1)')
    assert audit_rows(con, sql, 'id', 0)['violation_count'] == 1
    large = audit_rows(con, sql, 'id', 10**100, limit=10**100)
    assert large['expected_rows'] == 10**100
    example, = large['examples']
    assert example['output_rows'] == 1
