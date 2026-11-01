"""可核对的格式/出处校验。命中只代表有来源，不证明整句话的因果或语义。"""

from health_by.i18n import t

import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

# 校验判据独立于提示词；v2 将缺少原因段标为警告。
RULES_VERSION = "v2"
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
OUTPUT_KEYS = {"phenomenon", "causes", "advice"}
# 只在对应字段实际存在时启用中文/常用名称，缺测陈述不必写成日志。
FIELD_ALIASES = {
    "hrv_ms": ("HRV", "心率变异性"),
    "resting_hr": ("静息心率",),
    "breathing_rate": ("呼吸频率",),
    "spo2": ("血氧", "血氧饱和度"),
    "total_minutes": ("睡眠时长",),
    "sleep_minutes": ("睡眠时长",),
    "steps": ("步数",),
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


def _numbers(payload: dict) -> set[Decimal]:
    values: set[Decimal] = set()

    def walk(value, path=()):
        key = path[-1] if path else ""
        # 输入协议的时间后缀不改变数值的量纲或原有可换算规则。
        base_key = re.sub(r"_(today|7d)$", "", key)
        aggregate = base_key in {"mean", "median", "min", "max", "first", "last", "goal", "remaining"}
        duration = "minutes" in key or (aggregate and any("minutes" in part for part in path[:-1]))
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            number = Decimal(str(value))
            values.update((number, abs(number)))
            if duration and number >= 0:
                hours, minutes = divmod(number, 60)
                values.update((hours, minutes, number / 60))
            if ("ratio" in key or key == "achievement_rate") and number >= 0:
                values.add(number * 100)
        elif isinstance(value, str):
            values.update(Decimal(days) for days in re.findall(r"D[-+](\d+)", value))
        elif isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, (*path, child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, path)

    walk(payload)
    relative = re.fullmatch(r"D[-+](\d+)", str(payload.get("date")))
    if relative:
        values.add(Decimal(relative.group(1)))
    return values


def validate_output(text: str, payload: dict) -> tuple[dict, dict]:
    issues = []
    try:
        raw = _unpack_once(json.loads(text))
    except (ValueError, TypeError):
        raw = None
    valid_shape = isinstance(raw, dict) and set(raw) == OUTPUT_KEYS
    if not valid_shape:
        issues.append({"section": "output", "text": text, "reason": t('server.ai.validation.validate_output.f9dd8a6a1')})
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
                issues.append({"section": key, "text": rendered, "reason": t('server.ai.validation.validate_output.8c585402b')})
                content[key] = rendered
    candidates = _numbers(payload)
    fields = _field_references(payload)
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
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;])|\n+", value) if re.search(r"\w", s)]
        if not sentences:
            issues.append({"section": key, "text": value, "reason": t('server.ai.validation.validate_output.2fd463987')})
        for sentence in sentences:
            sentence_count += 1
            references = fields.findall(sentence) if fields else []
            referenced_fields.update(references)
            tokens = NUMBER.findall(sentence)
            sentence_matches = 0
            sentence_missed = []
            for token in tokens:
                numeric = token.rstrip("%").replace(",", "")
                number = abs(Decimal(numeric))
                precision = max(0, -number.as_tuple().exponent)
                quantum = Decimal(1).scaleb(-precision)
                with localcontext() as context:
                    context.prec = max(50, precision + 400)
                    found = any(abs(candidate).quantize(quantum, rounding=ROUND_HALF_UP) == number for candidate in candidates)
                if found:
                    sentence_matches += 1
                else:
                    sentence_missed.append(token)
            matched_numbers += sentence_matches
            matched += sentence_matches + len(references)
            missed.extend(sentence_missed)
            if not tokens and not references:
                unsupported.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.d0b418f3d')})
            elif tokens and not sentence_matches and not references:
                unsupported.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.21a0d6ff4')})
            elif sentence_missed:
                warnings.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.26bf596ba'), "missed": sentence_missed})
            if any(p in sentence for p in ("仅供参考", "不构成医疗建议", "建议咨询医生", "建议就医")):
                issues.append({"section": key, "text": sentence, "reason": t('server.ai.validation.validate_output.1f300729a')})
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
