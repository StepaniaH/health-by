import json

import pytest

from health_by.ai.validation import validate_output


def test_missing_cause_is_a_visible_warning_without_discarding_supported_content():
    content = {"phenomenon": "步数 1000 步。", "causes": None, "advice": None}
    parsed, result = validate_output(json.dumps(content), {"steps": 1000})
    assert parsed == content
    assert result["state"] == "warn"
    assert result["issues"] == []
    assert any(item["section"] == "causes" and item["reason"] == "缺少原因段" for item in result["warnings"])
    content["causes"] = "看不出，因为缺少 HRV 数据。"
    _, complete = validate_output(json.dumps(content), {"steps": 1000, "hrv_ms": None})
    assert complete["state"] == "ok"


@pytest.mark.parametrize("text,state,missed", [
    ("步数 12,345 步。", "ok", []),
    ("睡眠 4 小时 8 分。", "ok", []),
    ("HRV 22 ms。", "ok", []),
    ("比基线低 18%。", "ok", []),
    ("完成目标 193%。", "ok", []),
    ("步数 12345 步，另测 99999 步。", "warn", ["99999"]),
    ("HRV 99999 ms。", "warn", ["99999"]),
    ("状态似乎不错。", "failed", []),
])
def test_numbers_are_checked_against_input_and_common_equivalent_forms(text, state, missed):
    payload = {"date": "D-0", "sleep": {"total_minutes": 248}, "steps": 12345,
               "hrv_ms": 21.7, "hrv_delta_pct": -18, "goal": {"ratio": 1.931}}
    _, result = validate_output(json.dumps({"phenomenon": text, "causes": "依据 steps 记录分析。", "advice": None}), payload)
    assert result["state"] == state
    assert result["missed"] == missed


def test_invalid_shape_is_preserved_with_a_format_issue():
    text = '{"phenomenon":"步数 8000 步","causes":null,"advice":{"steps":8000}}'
    content, validation = validate_output(text, {"steps": 8000})
    assert validation["state"] == "failed"
    assert content["advice"] == '{"steps": 8000}'
    assert any(item["section"] == "advice" for item in validation["issues"])


@pytest.mark.parametrize("wrapper", ["root_string", "phenomenon_string", "field_string"])
def test_a_single_extra_json_encoding_layer_is_unpacked(wrapper):
    expected = {"phenomenon": "步数 8000 步。", "causes": "依据 steps 记录分析。", "advice": None}
    if wrapper == "root_string":
        raw = json.dumps(expected, ensure_ascii=False)
    elif wrapper == "phenomenon_string":
        raw = {"phenomenon": json.dumps(expected, ensure_ascii=False)}
    else:
        raw = {**expected, "phenomenon": json.dumps({"phenomenon": expected["phenomenon"]}, ensure_ascii=False)}
    content, validation = validate_output(json.dumps(raw, ensure_ascii=False), {"steps": 8000})
    assert content == expected
    assert validation["state"] == "ok"


def test_deeper_json_nesting_is_not_unpacked_recursively():
    raw = json.dumps(json.dumps({"phenomenon": "步数 8000 步。", "causes": None, "advice": None}))
    _, validation = validate_output(json.dumps(raw), {"steps": 8000})
    assert validation["state"] == "failed"


@pytest.mark.parametrize("sentence,state", [
    ("今天 sleep.total_minutes、hrv_ms、resting_hr、breathing_rate、spo2 均为 null。", "ok"),
    ("今天 HRV 与静息心率均为 null。", "ok"),
    ("今天睡眠时长、呼吸频率、血氧饱和度没有数据。", "ok"),
    ("今天hrv_ms没有数据。", "ok"),
    ("今天 day.heart.hrv_ms 为 null。", "ok"),
    ("今天 `heart.hrv_ms` 没有数据。", "ok"),
    ("hrv_ms 正常，resting_hr 偏低。", "ok"),  # 校验出处，不证明语义。
    ("今天 hrv_msx 为 null。", "failed"),
    ("今天 fake_hrv_ms 为 null。", "failed"),
    ("今天 wrong.hrv_ms 为 null。", "failed"),
    ("今天 heart.hrv_ms.extra 为 null。", "failed"),
    ("今天 nonexistent 为 null。", "failed"),
    ("今天数据似乎不错。", "failed"),
    ("hrv_ms 比基线相差 2.4。", "warn"),
    ("101.8 与 104.2 相差 2.4。", "warn"),
])
def test_real_fields_and_metric_names_are_sources_but_substrings_are_not(sentence, state):
    payload = {"day": {"sleep": {"total_minutes": None}, "heart": {
        "hrv_ms": None, "resting_hr": None, "breathing_rate": None, "spo2": None}},
        "a": 101.8, "b": 104.2}
    _, result = validate_output(json.dumps({"phenomenon": sentence, "causes": "看不出，因为 HRV 没有数据。", "advice": None}), payload)
    assert result["state"] == state
    if state == "warn":
        assert result["missed"] == ["2.4"]


def test_aliases_only_count_when_the_corresponding_field_exists():
    for text in ("今天 HRV 没有数据。", "今天静息心率未采到。", "今天 hrv_ms 为 null。"):
        _, result = validate_output(json.dumps({"phenomenon": text, "causes": None, "advice": None}), {"steps": 100})
        assert result["state"] == "failed"


def test_field_sources_do_not_bypass_shape_or_forbidden_text_checks():
    for content in ({"phenomenon": "hrv_ms 为 null。", "advice": {"bad": "hrv_ms"}, "causes": None},
                    {"phenomenon": "hrv_ms 为 null，建议就医。", "advice": None, "causes": None}):
        _, result = validate_output(json.dumps(content), {"hrv_ms": None})
        assert result["state"] == "failed"


@pytest.mark.parametrize('supported,unsupported,state', [
    (4, 0, 'ok'), (3, 1, 'warn'), (5, 2, 'warn'), (7, 3, 'warn'),
    (2, 1, 'failed'), (4, 2, 'failed'), (0, 1, 'failed'),
])
def test_unsupported_sentence_grades_respect_the_thirty_percent_boundary(supported, unsupported, state):
    # 将一条已有的有依据句移到原因段，不改变比例门槛测试的总句数。
    # 零依据用例刻意保留空原因，避免凭 fixture 增加一条依据。
    text = '步数 1000 步。' * max(0, supported - 1) + '今天状态不错。' * unsupported
    causes = '步数 1000 步。' if supported else None
    _, result = validate_output(json.dumps({'phenomenon': text, 'causes': causes, 'advice': None}), {'steps': 1000})
    assert result['state'] == state
    assert result['sentences'] == supported + unsupported
    assert result['unsupported_sentences'] == unsupported
    assert len(result['warnings'] if state == 'warn' else result['issues']) == unsupported


def test_field_only_evidence_still_counts_and_hard_failures_are_never_downgraded():
    content = {'phenomenon': 'hrv_ms 为 null。', 'causes': '看不出，因为 hrv_ms 没有数据。', 'advice': None}
    _, result = validate_output(json.dumps(content), {'hrv_ms': None})
    assert result['state'] == 'ok' and result['matched'] > 0 and result['matched_numbers'] == 0
    content['phenomenon'] = '步数 1000 步。' * 9 + '建议就医。'
    _, result = validate_output(json.dumps(content), {'steps': 1000})
    assert result['state'] == 'failed'
    assert any('禁止' in item['reason'] for item in result['issues'])
    content['advice'] = {'wrong': 'steps'}
    _, result = validate_output(json.dumps(content), {'steps': 1000})
    assert result['state'] == 'failed'


def test_window_duration_values_keep_units_but_coverage_counters_do_not_become_minutes():
    content = {'phenomenon': '睡眠中位数为 4 小时 8 分。', 'causes': '依据睡眠时长记录分析。', 'advice': None}
    _, result = validate_output(json.dumps(content), {'metrics': {'sleep': {'total_minutes': {'median': 248, 'coverage_days': 90}}}})
    assert result['state'] == 'ok' and result['missed'] == []
    content['phenomenon'] = '覆盖了 1.5 小时。'
    _, result = validate_output(json.dumps(content), {'metrics': {'sleep': {'total_minutes': {'median': None, 'coverage_days': 90}}}})
    assert result['state'] == 'failed'


@pytest.mark.parametrize("field,label", [
    ("hrv_ms", "HRV"), ("resting_hr", "静息心率"), ("breathing_rate", "呼吸频率"),
    ("spo2", "血氧"), ("total_minutes", "睡眠时长"), ("steps", "步数"),
])
def test_chinese_references_work_for_new_time_suffixes_without_an_unsuffixed_companion(field, label):
    from health_by.ai.validation import _field_references

    pattern = _field_references({"day": {field + "_today": None, field + "_7d": None}})
    assert pattern.search(label)
    wrong = _field_references({"day": {"fake_" + field + "_today": None}})
    assert not wrong.search(label)


def test_minimal_dual_fields_preserve_all_chinese_references_and_only_the_real_unsupported_sentence():
    payload = {"day": {
        "heart": {name + suffix: None for name in ("hrv_ms", "resting_hr", "breathing_rate", "spo2")
                  for suffix in ("_today", "_7d")},
        "sleep": {"total_minutes_today": None, "total_minutes_7d": None},
        "activity": {"steps_today": None},
    }}
    content = {"phenomenon": "心率变异性没有记录。静息心率没有记录。呼吸频率没有记录。血氧没有记录。睡眠时长没有记录。步数没有记录。保持作息。",
               "causes": "心率变异性没有记录。", "advice": None}
    _, validation = validate_output(json.dumps(content, ensure_ascii=False), payload)
    assert set(validation["referenced_fields"]) >= {"心率变异性", "静息心率", "呼吸频率", "血氧", "睡眠时长", "步数"}
    assert validation["unsupported_sentences"] == 1 and validation["state"] == "warn"
    assert [item["text"] for item in validation["warnings"]] == ["保持作息。"]
