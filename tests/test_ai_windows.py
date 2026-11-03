"""窗口口径与隐私合同：手算稀疏日历、零值、越界、端点与目标分母。"""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from health_by import db, storage
from health_by.ai import prompt
from health_by.ai.service import build_window_payload, payload_audit
from health_by.api.app import create_app
from health_by.util import iso_range, iso_today, shift_iso

HTTP_CLIENT = httpx.Client


def assert_scalars_only(value):
    assert not isinstance(value, list), '窗口出口不得携带逐日数组或排名'
    if isinstance(value, dict):
        for child in value.values():
            assert_scalars_only(child)


def sparse_days():
    return [
        {'date': '2026-10-01', 'activity': {'steps': 0}, 'heart': {'hrv_ms': 10}, 'sleep': {'total_minutes': 0}},
        {'date': '2026-10-03', 'activity': {'steps': 100}, 'sleep': {'total_minutes': 480}},
        {'date': '2026-10-04', 'profile': {'name': 'PRIVATE-CANARY'}},
        {'date': '2026-10-05', 'activity': {'steps': 300}, 'heart': {'hrv_ms': 30}, 'sleep': {'total_minutes': 960}},
        {'date': '2026-10-07', 'heart': {'hrv_ms': 999}},
    ]


def test_sparse_window_statistics_use_calendar_days_and_preserve_recorded_zero():
    result = build_window_payload(sparse_days(), {'steps': 200, 'sleep_minutes': 480},
                                  '2026-10-01', '2026-10-07', today='2026-10-07')
    assert result['window'] == {'days': 7, 'days_with_data': 3, 'days_with_archive': 5,
                                'missing_days': 4, 'max_missing_streak': 2, 'date_range': 'D-6 → D-0'}
    steps = result['metrics']['activity']['steps']
    assert (steps['mean'], steps['median'], steps['min'], steps['max']) == (133.3333, 100, 0, 300)
    assert (steps['coverage_days'], steps['missing_days'], steps['max_missing_streak']) == (3, 4, 2)
    assert (steps['min_day'], steps['max_day']) == ('D-6', 'D-2')
    assert steps['trend'] == {'direction': 'up', 'first': 0, 'last': 300}
    hrv = result['metrics']['heart']['hrv_ms']
    assert hrv['mean'] == hrv['median'] == 20
    assert (hrv['coverage_days'], hrv['missing_days'], hrv['excluded_days'], hrv['max_missing_streak']) == (2, 5, 1, 3)
    goals = result['goals']
    assert goals['steps']['achievement_rate'] == 0.3333  # 一天达标 / 三天测到，不能除以七。
    assert goals['sleep_minutes']['achievement_rate'] == 0.6667
    assert goals['sleep_minutes']['best_day'] == 'D-4'  # 达标比例封顶，超长睡眠不比达标更“好”。
    assert goals['sleep_minutes']['best_ratio'] == 1
    assert_scalars_only(result)


def test_missing_measurements_remain_null_and_do_not_create_zero_trends_or_extrema():
    result = build_window_payload([{'date': '2026-10-02', 'activity': {'steps': 10}}], {},
                                  '2026-10-01', '2026-10-03', today='2026-10-03')
    sleep = result['metrics']['sleep']['total_minutes']
    assert all(sleep[key] is None for key in ('mean', 'median', 'min', 'max', 'min_day', 'max_day'))
    assert sleep['coverage_days'] == 0 and sleep['missing_days'] == sleep['max_missing_streak'] == 3
    assert sleep['trend'] == {'direction': None, 'first': None, 'last': None}
    assert result['metrics']['activity']['steps']['trend'] == {'direction': None, 'first': 10, 'last': 10}
    assert result['goals']['sleep_minutes']['achievement_rate'] is None
    assert result['window']['days_with_data'] == 1


def test_window_whitelist_drops_private_and_future_fields_and_outside_dates():
    private = 'PRIVATE-CANARY'
    day = {'date': '2026-10-02', 'profile': private, 'device': {'id': private},
           'activity': {'steps': 20, 'future_field': private, 'steps_intraday': [private] * 3},
           'sleep': {'start_time': private, 'stage_timeline': [private]}, 'future_field': private}
    out = {'date': '2026-09-30', 'activity': {'steps': 99999999}}
    result = build_window_payload([day, out], {'steps': 100, 'identity': private},
                                  '2026-10-01', '2026-10-03', today='2026-10-03')
    serialized = json.dumps(result, allow_nan=False)
    assert private not in serialized and '2026-' not in serialized and '99999999' not in serialized
    assert_scalars_only(result)
    audit = payload_audit(result)
    assert not any('[]' in key or 'intraday' in key or 'profile' in key for key in audit['payload_keys'])


@pytest.fixture
def window_api(synthetic_cfg, monkeypatch):
    app = create_app(synthetic_cfg)
    api = TestClient(app)
    api.post('/api/v1/settings/ai', json={'ai_enabled': True, 'ai_base_url': 'https://opencode.ai/zen/go/v1',
                                      'ai_model': 'window-model', 'api_key': 'synthetic-key'})
    app.state.engine.sync_days(iso_range(iso_today(), 10))
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'].split('\n', 1)[1].rsplit('\n', 1)[0])
        assert_scalars_only(payload)
        content = {'phenomenon': f"所选窗口共 {payload['window']['days']} 天。", 'causes': None, 'advice': None}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr('health_by.ai.client.httpx.Client', lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    yield app, api, requests
    app.state.engine.shutdown()
    app.state.engine.close()
    app.state.conn.close()


def test_legacy_date_and_explicit_single_day_share_cache_and_ranges_have_their_own_sessions(window_api):
    app, api, requests = window_api
    end = iso_today()
    first = api.post('/api/v1/ai/analyze', json={'date': end}).json()
    assert first['start_date'] == first['end_date'] == end and first['attempts'] == 1
    assert api.post('/api/v1/ai/analyze', json={'start': end, 'end': end}).json()['cached'] is True
    start = shift_iso(end, -6)
    ranged = api.post('/api/v1/ai/analyze', json={'start': start, 'end': end}).json()
    assert ranged['start_date'] == start and ranged['end_date'] == end and ranged['cached'] is False
    assert api.get('/api/v1/ai/analysis', params={'start': start, 'end': end}).json()['analysis']['cached'] is True
    assert len(requests) == 2
    assert api.post('/api/v1/ai/analyze', json={'start': start, 'end': end, 'force': True}).status_code == 200
    assert requests[1].headers['x-opencode-session'] == requests[2].headers['x-opencode-session']
    assert requests[0].headers['x-opencode-session'] != requests[1].headers['x-opencode-session']
    assert api.post('/api/v1/ai/analyze', json={'start': shift_iso(end, -7), 'end': end}).status_code == 200
    assert requests[3].headers['x-opencode-session'] != requests[2].headers['x-opencode-session']
    rows = app.state.conn.execute('SELECT start_date, end_date FROM ai_analyses').fetchall()
    assert len(rows) == 4 and all(row['start_date'] and row['end_date'] for row in rows)
    assert len(api.get('/api/v1/export').json()['ai_analyses']) == 4


@pytest.mark.parametrize('body', [
    {'start': '2026-10-02'}, {'end': '2026-10-02'}, {'start': '2026-10-03', 'end': '2026-10-02'},
    {'start': '2025-01-01', 'end': '2026-10-02'}, {'date': '2026-10-01', 'start': '2026-10-01', 'end': '2026-10-02'},
    {'start': '2026-02-30', 'end': '2026-03-01'}, {'start': True, 'end': '2026-10-01'},
])
def test_invalid_windows_never_call_a_provider(window_api, body):
    _, api, requests = window_api
    assert api.post('/api/v1/ai/analyze', json=body).status_code == 400
    assert requests == []


def test_range_end_may_be_missing_but_an_empty_range_fails_before_network(window_api):
    _, api, requests = window_api
    end = shift_iso(iso_today(), 1)
    result = api.post('/api/v1/ai/analyze', json={'start': shift_iso(end, -6), 'end': end})
    assert result.status_code == 200 and result.json()['end_date'] == end
    before = len(requests)
    assert api.post('/api/v1/ai/analyze', json={'start': '2000-01-01', 'end': '2000-01-07'}).status_code == 404
    assert len(requests) == before


def test_v5_upgrade_backfills_window_dates_without_rewriting_historical_content(tmp_path):
    with closing(db.connect(tmp_path / 'v5.db')) as conn:
        for version, sql in db.MIGRATIONS:
            if version <= 5:
                conn.executescript(sql)
        conn.execute("INSERT INTO meta VALUES ('schema_version','5')")
        conn.execute("INSERT INTO ai_analyses (date,model,prompt_version,created_at,base_url,status,raw_content) "
                     "VALUES ('2026-10-31','old','v3','now','https://model.example/v1','ok','keep historical text')")
        conn.commit()
        db.migrate(conn)
        db.migrate(conn)
        row = conn.execute('SELECT * FROM ai_analyses').fetchone()
        assert row['start_date'] == row['end_date'] == row['date'] == '2026-10-31'
        assert row['raw_content'] == 'keep historical text' and row['prompt_version'] == 'v3'


def test_window_output_shape_is_constant_in_window_length_and_has_no_intermediate_series():
    short = build_window_payload(sparse_days(), {'steps': 200}, '2026-10-01', '2026-10-07', today='2026-11-01')
    long = build_window_payload(sparse_days(), {'steps': 200}, '2026-08-04', '2026-11-01', today='2026-11-01')
    assert set(short) == {'window', 'metrics', 'goals'}
    assert set(short['window']) == {'days', 'days_with_data', 'days_with_archive', 'missing_days', 'max_missing_streak', 'date_range'}
    assert set(short['metrics']['activity']['steps']) == {'mean', 'median', 'min', 'max', 'min_day', 'max_day',
        'coverage_days', 'missing_days', 'max_missing_streak', 'excluded_days', 'trend'}
    assert set(short['metrics']['activity']['steps']['trend']) == {'direction', 'first', 'last'}
    assert set(payload_audit(short)['payload_keys']) == set(payload_audit(long)['payload_keys'])
    assert_scalars_only(short)
    assert_scalars_only(long)


def test_range_repair_reuses_identical_aggregate_and_has_no_day_array_in_either_attempt(window_api, monkeypatch):
    app, api, requests = window_api
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'].split('\n', 1)[1].rsplit('\n', 1)[0])
        assert_scalars_only(payload)
        text = '今天状态不错。' if len(requests) == 1 else f"所选窗口 {payload['window']['days']} 天。"
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'phenomenon': text, 'causes': '依据 window.days 判断覆盖范围。' if len(requests) == 2 else None, 'advice': None})}}]})
    monkeypatch.setattr('health_by.ai.client.httpx.Client', lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    end = iso_today()
    result = api.post('/api/v1/ai/analyze', json={'start': shift_iso(end, -3), 'end': end}).json()
    assert result['attempts'] == len(requests) == 2 and result['validation']['state'] == 'ok'
    bodies = [json.loads(request.content) for request in requests]
    assert bodies[0]['messages'][1] == bodies[1]['messages'][1]
    rows = app.state.conn.execute('SELECT * FROM ai_egress ORDER BY attempt').fetchall()
    assert rows[0]['payload_hash'] == rows[1]['payload_hash']
    assert rows[0]['payload_keys_hash'] == rows[1]['payload_keys_hash']
    assert rows[0]['request_hash'] != rows[1]['request_hash']


def test_v6_upgrade_labels_both_protocols_without_rewriting_historical_records(tmp_path):
    with closing(db.connect(tmp_path / "v6.db")) as conn:
        for version, sql in db.MIGRATIONS:
            if version <= 6:
                conn.executescript(sql)
        conn.execute("INSERT INTO meta VALUES ('schema_version','6')")
        for start, end, version in [("2024-02-28", "2024-02-28", "v4"), ("2024-02-01", "2024-02-28", "v4-window-1")]:
            record = conn.execute(
                "INSERT INTO ai_analyses (date,start_date,end_date,model,prompt_version,created_at,base_url,status,raw_content)"
                " VALUES (?,?,?,?,?,'then','https://model.example/v1','ok','KEEP-TEXT')",
                (end,start,end,"synthetic",version),
            ).lastrowid
            conn.execute(
                "INSERT INTO ai_egress (analysis_id,attempt,payload_keys_json,payload_keys_hash,payload_hash,payload_shapes_json,"
                "request_hash,status,sent_at) VALUES (?,1,'[]','KEEP-KEYS-HASH','KEEP-PAYLOAD-HASH','{}','request','ok','then')",
                (record,),
            )
        conn.commit()
        old = [dict(row) for row in conn.execute("SELECT * FROM ai_analyses ORDER BY id")]
        old_egress = [dict(row) for row in conn.execute("SELECT * FROM ai_egress ORDER BY id")]
        db.migrate(conn)
        db.migrate(conn)
        rows = [dict(row) for row in conn.execute("SELECT * FROM ai_analyses ORDER BY id")]
        egress = [dict(row) for row in conn.execute("SELECT * FROM ai_egress ORDER BY id")]
        assert [row.pop("payload_schema_version") for row in rows] == ["daily-v1", "window-v1"]
        assert [row.pop("payload_schema_version") for row in egress] == ["daily-v1", "window-v1"]
        assert rows == old and egress == old_egress
