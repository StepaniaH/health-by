"""可核对的格式/出处校验。命中只代表有来源，不证明整句话的因果或语义。"""

from health_by.i18n import t

import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

# 校验判据独立于提示词；v3 增加围栏 JSON、结构化 code、符号与单位绑定及临床安全校验。
RULES_VERSION = "v3"
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
LIST_PREFIX = re.compile(r"^\s*\d+\s*[.、)．](?!\d)\s*")
NUMBER_UNIT = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<num>[-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:"
    r"(?P<ms>ms|毫秒)"
    r"|(?P<bpm>bpm|次/分)"
    r"|(?P<steps>步)"
    r"|(?P<kg>kg|公斤)"
    r"|(?P<km>km|公里)"
    r"|(?P<kcal>kcal|千卡|大卡)"
    r"|(?P<hours>小时|h(?![A-Za-z]))"
    r"|(?P<minutes>分钟|min(?![A-Za-z]))"
    r")",
    re.I,
)
POSITIVE_DIRECTION = re.compile(r"(?:高出|高于|上升|增加|多了|升高|偏高)\s*(?:约\s*)?(\d+(?:,\d{3})*(?:\.\d+)?)\s*%?")
NEGATIVE_DIRECTION = re.compile(r"(?:低于|下降|减少|少了|降低|偏低)\s*(?:约\s*)?(\d+(?:,\d{3})*(?:\.\d+)?)\s*%?")
REPAIR_FALLBACK_ADVICE = re.compile(r"^本轮没有可执行的建议[。.]?$")
BOILERPLATE_RE = re.compile(r"仅供参考|不构成医疗建议")
SEEK_CARE_RE = re.compile(r"建议咨询医生|建议就医|就医检查")
CLINICAL_SAFETY_RE = re.compile(
    r"(?:确诊|诊断为|患有|属于)(?:房颤|心律失常|心律不齐|心脏病|呼吸暂停|贫血|感染|抑郁|焦虑症)"
    r"|(?i:atrial\s+fibrillation|diagnos(?:e|ed\s+with|is\s+of)|prescribe)"
    r"|(?:建议|可以|需)(?:服用|停用|加量|减量|使用).*?(?:毫克|mg|片|粒|药物|处方|阿司匹林|β受体阻滞剂)"
)
OUTPUT_KEYS = {"phenomenon", "causes", "advice"}
# 只在对应字段实际存在时启用中文/常用名称，缺测陈述不必写成日志。
FIELD_ALIASES = {
    "hrv_ms": ("HRV", "心率变异性"),
    "resting_hr": ("静息心率", "RHR"),
    "breathing_rate": ("呼吸频率",),
    "spo2": ("血氧", "血氧饱和度", "SpO2"),
    "total_minutes": ("睡眠时长",),
    "sleep_minutes": ("睡眠时长",),
    "deep": ("深睡",),
    "light": ("浅睡",),
    "rem": ("REM睡眠", "快速眼动"),
    "efficiency": ("睡眠效率",),
    "steps": ("步数",),
    "distance_km": ("距离",),
    "calories_out": ("消耗", "卡路里"),
    "total_active_minutes": ("活跃时长", "活跃分钟"),
    "active_minutes": ("活跃时长", "活跃分钟"),
    "sedentary_minutes": ("久坐",),
    "skin_temp_delta_c": ("皮温", "皮肤温度"),
    "skin_temp_delta": ("皮温", "皮肤温度"),
    "weight_kg": ("体重",),
    "body_fat_pct": ("体脂", "体脂率"),
    "body_fat": ("体脂", "体脂率"),
    "irregular_rhythm_alerts": ("心律异常提醒", "不规则心律提醒"),
}

# 新输入仅改时间口径后缀，中文指标名的出处识别保持原语义。
FIELD_ALIASES.update({
    base + suffix: aliases
    for base, aliases in list(FIELD_ALIASES.items())
    for suffix in ("_today", "_7d")
})


def _field_references(payload: dict) -> re.Pattern | None:
    names = set()

    def walk(value, path=()):
        if isinstance(value, dict):
            if path and (path[-1] in FIELD_ALIASES or ('mean' in value and 'coverage_days' in value)):
                names.update(".".join(path[index:]) for index in range(len(path)))
                names.update(FIELD_ALIASES.get(path[-1], ()))
            for key, child in value.items():
                walk(child, (*path, key))
        elif isinstance(value, list):
            for child in value:
                walk(child, path)
        elif path:
            # 接受完整路径、真实后缀路径和叶子名；不会凭空生成其它路径。
            names.update(".".join(path[index:]) for index in range(len(path)))
            names.update(FIELD_ALIASES.get(path[-1], ()))

    walk(payload)
    if not names:
        return None
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=lambda name: (-len(name), name)))
    return re.compile(r"(?<![A-Za-z0-9_.])(?:" + alternatives + r")(?![A-Za-z0-9_.])")


def _unpack_once(raw):
    """只解一层模型的重复编码，保留无法符合三字段契约的结构供标记。"""
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            return raw
        return decoded if isinstance(decoded, dict) else raw
    if not isinstance(raw, dict):
        return raw
    unpacked = dict(raw)
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        try:
            decoded = json.loads(value)
        except ValueError:
            continue
        if not isinstance(decoded, dict):
            continue
        if key == "phenomenon" and set(decoded) == OUTPUT_KEYS and all(
            raw.get(other) is None for other in ("causes", "advice")
        ):
            return decoded
        unpacked[key] = decoded.get(key) if set(decoded) == {key} else decoded
    return unpacked


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


def _match_number(number: Decimal, candidates: set[Decimal]) -> bool:
    precision = max(0, -number.as_tuple().exponent)
    quantum = Decimal(1).scaleb(-precision)
    with localcontext() as context:
        context.prec = max(50, precision + 400)
        return any(candidate.quantize(quantum, rounding=ROUND_HALF_UP) == number for candidate in candidates)


def _match_abs(number: Decimal, candidates: set[Decimal]) -> bool:
    precision = max(0, -number.as_tuple().exponent)
    quantum = Decimal(1).scaleb(-precision)
    with localcontext() as context:
        context.prec = max(50, precision + 400)
        return any(abs(candidate).quantize(quantum, rounding=ROUND_HALF_UP) == number for candidate in candidates)


def _has_irregular_rhythm_alert(payload: dict) -> bool:
    alerts = ((payload.get("day") or {}).get("alerts")) if isinstance(payload, dict) else None
    if isinstance(alerts, dict):
        for key in ("irregular_rhythm_alerts", "irregular_rhythm_alerts_today"):
            val = alerts.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return True
    metrics_alerts = ((payload.get("metrics") or {}).get("alerts") or {}).get("irregular_rhythm_alerts")
    if isinstance(metrics_alerts, dict):
        high = metrics_alerts.get("max")
        if isinstance(high, (int, float)) and high > 0:
            return True
    return False


_STAT_LEAVES = {"current", "baseline", "difference", "mean", "median", "min", "max", "first", "last"}
_HR_LEAVES = {"resting_hr", "hr_min", "hr_max", "hr_avg", "non_rem_hr"}
_DURATION_LEAVES = {
    "total_minutes", "sleep_minutes", "deep", "light", "rem", "wake",
    "minutes_awake", "minutes_to_fall_asleep", "naps_minutes",
    "total_active_minutes", "light_active_minutes", "moderate_active_minutes",
    "vigorous_active_minutes", "zone_minutes", "sedentary_minutes",
    "need_minutes", "sleep_debt", "avg_deficit", "sleep_7d",
    "active_avg_7", "active_avg_28", "who_weighted_minutes", "who_weekly_minutes",
    "midpoint_sd_min", "bed_sd_min", "wake_sd_min", "social_jetlag_min",
}


def _metric_unit_family(path: tuple[str, ...]) -> str | None:
    if not path:
        return None
    key = re.sub(r"_(today|7d)$", "", path[-1])
    if key in _STAT_LEAVES and len(path) >= 2:
        metric = re.sub(r"_(today|7d)$", "", path[-2])
    else:
        metric = key
    if key in {"sample_count", "window_days", "min_samples", "coverage_days", "missing_days",
               "max_missing_streak", "excluded_days", "percent_change", "hrv_delta_pct",
               "ratio", "achievement_rate", "r", "n", "offset_days", "ci_low", "ci_high"}:
        return None
    if metric in {"hrv_ms", "hrv_deep_rmssd_ms"}:
        return "ms"
    if metric in _HR_LEAVES or key == "rhr_delta":
        return "bpm"
    if metric in {"steps", "steps_avg_7", "steps_avg_28", "steps_7d"} or (
        len(path) >= 2 and path[-2] == "steps" and key in {"goal", "remaining"}
    ):
        return "steps"
    if metric == "weight_kg":
        return "kg"
    if metric == "distance_km":
        return "km"
    if metric in {"calories_out", "calories_in"}:
        return "kcal"
    if metric in _DURATION_LEAVES or (len(path) >= 2 and path[-2] == "sleep_debt" and key == "minutes") or (
        len(path) >= 2 and path[-2] == "sleep_minutes" and key in {"goal", "remaining"}
    ):
        return "duration"
    return None


def _is_signed_delta_key(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    base = re.sub(r"_(today|7d)$", "", path[-1])
    if base in {"difference", "percent_change", "hrv_delta_pct", "rhr_delta", "skin_temp_delta", "skin_temp_delta_c"}:
        return True
    return path[-1] in {"steps_7d", "sleep_7d"}


def _numbers(payload: dict) -> tuple[set[Decimal], set[Decimal], set[Decimal], set[Decimal], dict[str, set[Decimal]]]:
    signed_values: set[Decimal] = set()
    abs_values: set[Decimal] = set()
    signed_deltas: set[Decimal] = set()
    deficit_positive: set[Decimal] = set()
    metric_values: dict[str, set[Decimal]] = {
        "ms": set(), "bpm": set(), "steps": set(), "kg": set(), "km": set(), "kcal": set(), "hours": set(), "minutes": set(),
    }

    def walk(value, path=()):
        key = path[-1] if path else ""
        # 输入协议的时间后缀不改变数值的量纲或原有可换算规则。
        base_key = re.sub(r"_(today|7d)$", "", key)
        aggregate = base_key in {"mean", "median", "min", "max", "first", "last", "goal", "remaining"}
        duration = (
            "minutes" in key
            or (aggregate and any("minutes" in part for part in path[:-1]))
            or key in {"avg_deficit", "sleep_7d"}
            or ("sleep_minutes" in path and base_key in {"current", "baseline", "difference"})
        )
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            number = Decimal(str(value))
            signed_values.add(number)
            abs_values.update((number, abs(number)))
            if _is_signed_delta_key(path):
                signed_deltas.add(number)
            if (len(path) >= 2 and path[-2] == "sleep_debt" and key in {"minutes", "avg_deficit"}) or base_key == "remaining":
                deficit_positive.add(abs(number))
            family = _metric_unit_family(path)
            if duration:
                abs_num = abs(number)
                hours, minutes = divmod(abs_num, 60)
                abs_values.update((hours, minutes, abs_num / 60))
                signed_values.add(number / 60)
                if number < 0:
                    signed_values.update((-hours, -minutes))
                else:
                    signed_values.update((hours, minutes))
                if family == "duration":
                    metric_values["minutes"].update((abs_num, minutes))
                    metric_values["hours"].update((hours, abs_num / 60))
            elif family in metric_values:
                metric_values[family].add(abs(number))
            if ("ratio" in key or key == "achievement_rate" or base_key == "ratio") and number >= 0:
                abs_values.add(number * 100)
                signed_values.add(number * 100)
        elif isinstance(value, str):
            for days in re.findall(r"D[-+](\d+)", value):
                d = Decimal(days)
                abs_values.add(d)
                signed_values.add(d)
        elif isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, (*path, child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, path)

    walk(payload)
    relative = re.fullmatch(r"D[-+](\d+)", str(payload.get("date")))
    if relative:
        d = Decimal(relative.group(1))
        abs_values.add(d)
        signed_values.add(d)
    return signed_values, abs_values, signed_deltas, deficit_positive, metric_values


def validate_output(text: str, payload: dict) -> tuple[dict, dict]:
    issues = []
    try:
        raw = _unpack_once(json.loads(_strip_code_fence(text)))
    except (ValueError, TypeError):
        raw = None
    valid_shape = isinstance(raw, dict) and set(raw) == OUTPUT_KEYS
    if not valid_shape:
        issues.append({"section": "output", "code": "invalid_json_shape", "text": text, "reason": t('server.ai.validation.validate_output.f9dd8a6a1')})
        content = {"phenomenon": text, "causes": None, "advice": None}
    else:
        content = {}
        for key, value in raw.items():
            if value is None and key != "phenomenon":
                content[key] = None
            elif isinstance(value, str) and value.strip():
                content[key] = value
            else:
                rendered = json.dumps(value, ensure_ascii=False)
                issues.append({"section": key, "code": "invalid_field_type", "text": rendered, "reason": t('server.ai.validation.validate_output.8c585402b')})
                content[key] = rendered
    signed_candidates, abs_candidates, signed_deltas, deficit_positive, metric_values = _numbers(payload)
    fields = _field_references(payload)
    irregular_alert = _has_irregular_rhythm_alert(payload)
    missed = []
    matched = 0
    matched_numbers = 0
    sentence_count = 0
    unsupported = []
    warnings = []
    referenced_fields = set()
    for key, value in content.items():
        if value is None:
            continue
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[。！？!?；;])|(?<=[A-Za-z0-9_]{2}[.])\s+(?=(?:\d+\s*[.、)．](?!\d)\s*)?[A-Z\u4e00-\u9fff])|\n+", value)
            if re.search(r"\w", s)
        ]
        if not sentences:
            issues.append({"section": key, "code": "empty_section", "text": value, "reason": t('server.ai.validation.validate_output.2fd463987')})
        for sentence in sentences:
            sentence_count += 1
            body = LIST_PREFIX.sub("", sentence)
            if REPAIR_FALLBACK_ADVICE.match(body.strip()):
                matched += 1
                continue
            references = fields.findall(sentence) if fields else []
            referenced_fields.update(references)
            tokens = NUMBER.findall(body)
            sentence_matches = 0
            sentence_missed = []
            for token in tokens:
                numeric = token.rstrip("%").replace(",", "")
                signed_val = Decimal(numeric)
                number = abs(signed_val)
                found = _match_abs(number, abs_candidates)
                if found:
                    sentence_matches += 1
                    if token.startswith("-") and not _match_number(signed_val, signed_candidates):
                        issues.append({
                            "section": key, "code": "sign_mismatch", "text": sentence,
                            "reason": t('server.ai.validation.validate_output.sign_mismatch'),
                        })
                    elif token.startswith("+") and not _match_number(signed_val, {v for v in signed_candidates if v > 0}):
                        issues.append({
                            "section": key, "code": "sign_mismatch", "text": sentence,
                            "reason": t('server.ai.validation.validate_output.sign_mismatch'),
                        })
                else:
                    sentence_missed.append(token)
            for match in NUMBER_UNIT.finditer(body):
                family = match.lastgroup
                if not family:
                    continue
                unit_num = abs(Decimal(match.group("num").replace(",", "")))
                if _match_abs(unit_num, abs_candidates) and not _match_abs(unit_num, metric_values.get(family, set())):
                    issues.append({
                        "section": key, "code": "metric_unit_mismatch", "text": sentence,
                        "reason": t('server.ai.validation.validate_output.metric_unit_mismatch'),
                    })
                    break
            for raw_dir in POSITIVE_DIRECTION.findall(body):
                dir_num = Decimal(raw_dir.replace(",", ""))
                if _match_abs(dir_num, {v for v in signed_deltas if v < 0}) and not _match_abs(dir_num, {v for v in signed_deltas if v > 0}):
                    issues.append({
                        "section": key, "code": "sign_mismatch", "text": sentence,
                        "reason": t('server.ai.validation.validate_output.sign_mismatch'),
                    })
                    break
            for raw_dir in NEGATIVE_DIRECTION.findall(body):
                dir_num = Decimal(raw_dir.replace(",", ""))
                if (
                    _match_abs(dir_num, {v for v in signed_deltas if v > 0})
                    and not _match_abs(dir_num, {v for v in signed_deltas if v < 0})
                    and not _match_abs(dir_num, deficit_positive)
                ):
                    issues.append({
                        "section": key, "code": "sign_mismatch", "text": sentence,
                        "reason": t('server.ai.validation.validate_output.sign_mismatch'),
                    })
                    break
            matched_numbers += sentence_matches
            matched += sentence_matches + len(references)
            missed.extend(sentence_missed)
            if not tokens and not references:
                unsupported.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.d0b418f3d')})
            elif tokens and not sentence_matches and not references:
                unsupported.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.21a0d6ff4')})
            elif sentence_missed:
                warnings.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.26bf596ba'), "missed": sentence_missed})
            if CLINICAL_SAFETY_RE.search(sentence):
                issues.append({"section": key, "code": "clinical_safety", "text": sentence, "reason": t('server.ai.validation.validate_output.clinical_safety')})
            elif BOILERPLATE_RE.search(sentence) or (not irregular_alert and SEEK_CARE_RE.search(sentence)):
                issues.append({"section": key, "code": "banned_boilerplate", "text": sentence, "reason": t('server.ai.validation.validate_output.1f300729a')})
    # 比例门槛优先：1/3 仍失败，1/4 与 3/10 只警告。字段引用同样计入命中依据。
    if matched == 0 or len(unsupported) * 10 > sentence_count * 3:
        issues.extend(unsupported)
    else:
        warnings.extend(unsupported)
    cause = raw.get("causes") if isinstance(raw, dict) else None
    if cause is None or isinstance(cause, str) and not cause.strip():
        warnings.append({"section": "causes", "text": "", "reason": t('server.ai.validation.validate_output.2c861db27')})
    state = "failed" if issues or matched == 0 else ("warn" if warnings or missed else "ok")
    return content, {
        "state": state,
        "issues": issues, "warnings": warnings, "missed": list(dict.fromkeys(missed)), "matched": matched,
        "referenced_fields": sorted(referenced_fields),
        "matched_numbers": matched_numbers, "sentences": sentence_count,
        "unsupported_sentences": len(unsupported),
    }
