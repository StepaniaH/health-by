"""AI 数据出口白名单；新字段默认不外发。"""

from __future__ import annotations

from health_by.i18n import t, using_language

import hashlib
import json
import re
from statistics import mean
import threading
import time
from contextlib import closing
from datetime import date as date_type

from .. import analysis as analysis_module, db, insights as insights_module, metrics, reliability, storage
from ..config import Config
from ..secrets import SecretError, SecretStore
from ..util import iso_range, iso_today, utc_now_iso
from . import prompt, schema
from .client import AIClient, AIError, AI_ERROR_TEXT, USER_AGENT, ANALYSIS_MAX_TOKENS, validate_base_url
from .validation import RULES_VERSION, validate_output


def _numbers(value: dict | None, keys: tuple[str, ...]) -> dict:
    source = value if isinstance(value, dict) else {}
    return {key: metrics.to_float(source.get(key)) for key in keys}


def _legacy_payload(day: dict, goals: dict, analysis: dict, insights: dict, *, today: str | None = None) -> dict:
    """唯一出口：标量、固定形状的统计与已通过校正的关系，不复制源对象。"""
    selected = date_type.fromisoformat(day["date"])
    age = (date_type.fromisoformat(today or iso_today()) - selected).days
    sleep = day.get("sleep") or {}
    activity = dict(day.get("activity") or {})
    activity["total_active_minutes"] = metrics.total_active_minutes(
        metrics.to_float(activity.get("light_active_minutes")),
        metrics.to_float(activity.get("moderate_active_minutes")),
        metrics.to_float(activity.get("vigorous_active_minutes")),
    )
    recovery = insights.get("recovery") or {}
    coverage = insights.get("coverage") or {}
    comparisons = analysis.get("comparisons") or {}
    progress = analysis.get("goal_progress") or {}
    goal_progress = {}
    for key in ("steps", "sleep_minutes"):
        item = progress.get(key) or {}
        value = metrics.to_float(item.get("value"))
        goal = metrics.to_float(item.get("goal"))
        goal_progress[key] = {
            "ratio": metrics.to_float(item.get("ratio")),
            "remaining": max(0, goal - value) if goal is not None and value is not None else None,
        }
    metric_keys = ("steps", "sleep_minutes", "resting_hr", "hrv_ms", "breathing_rate", "spo2", "skin_temp_delta")
    correlation_keys = {
        "sleep_minutes", "deep_minutes", "sleep_efficiency", "sleep_mid", "steps",
        "active_minutes", "sedentary_minutes", "resting_hr", "hrv_ms",
    }
    correlations = []
    for item in insights.get("correlations") or []:
        if not item.get("survives_correction") or item.get("x") not in correlation_keys or item.get("y") not in correlation_keys:
            continue
        correlations.append({
            "x": item["x"], "y": item["y"],
            **_numbers(item, ("offset_days", "r", "n", "ci_low", "ci_high")),
        })
    return {
        "date": f"D-{age}" if age >= 0 else f"D+{-age}",
        "weekday": schema.MODEL_WEEKDAYS[selected.weekday()],
        "day": {
            "activity": _numbers(activity, (
                "steps", "distance_km", "calories_out", "floors", "total_active_minutes",
                "light_active_minutes", "moderate_active_minutes", "vigorous_active_minutes",
                "zone_minutes", "sedentary_minutes",
            )),
            "heart": _numbers(day.get("heart"), (
                "resting_hr", "hrv_ms", "spo2", "spo2_min", "spo2_max", "breathing_rate",
                "vo2max", "hr_min", "hr_max", "hr_avg", "skin_temp_delta_c",
                "skin_temp_nightly_c", "skin_temp_baseline_c", "skin_temp_stddev_30d_c",
                "core_temp_c", "non_rem_hr", "hrv_deep_rmssd_ms", "hrv_entropy",
            )),
            "sleep": {
                **_numbers(sleep, ("total_minutes", "efficiency", "minutes_awake", "minutes_to_fall_asleep")),
                "stages": _numbers(sleep.get("stages"), ("deep", "light", "rem", "wake")),
            },
            "body": _numbers(day.get("body"), ("weight_kg", "body_fat_pct", "water_ml", "calories_in")),
            **_numbers(day, ("naps_count", "naps_minutes")),
            "alerts": _numbers(day.get("alerts"), ("irregular_rhythm_alerts",)),
        },
        "goals": _numbers(goals, ("steps", "sleep_minutes")),
        "statistics": {
            **_numbers(insights, ("window_days", "n_days")),
            "comparisons": {
                key: _numbers(comparisons.get(key), (
                    "current", "baseline", "difference", "percent_change", "sample_count", "window_days", "min_samples",
                )) for key in metric_keys
            },
            "period_deltas": _numbers(analysis.get("period_deltas"), ("steps_7d", "sleep_7d")),
            "goal_progress": goal_progress,
            "bands_outside": {
                key: _numbers((insights.get("bands_outside") or {}).get(key), ("points", "outside", "percent"))
                for key in ("steps", "sleep_minutes", "hrv_ms", "resting_hr", "active_minutes")
            },
            "recovery": {
                **_numbers(recovery, ("need_minutes", "hrv_delta_pct", "rhr_delta")),
                "sleep_debt": _numbers(recovery.get("sleep_debt"), ("minutes", "nights", "window", "avg_deficit")),
            },
            "load": _numbers(insights.get("load"), (
                "steps_avg_7", "steps_avg_28", "steps_ratio", "active_avg_7", "active_avg_28",
                "active_ratio", "who_weighted_minutes", "who_weekly_minutes", "who_pct", "window_days",
            )),
            "rhythm": _numbers(insights.get("rhythm"), (
                "sri", "sri_pairs", "midpoint_sd_min", "bed_sd_min", "wake_sd_min",
                "social_jetlag_min", "n_nights", "rhythm_days", "baseline_days",
            )),
            "coverage": {
                **_numbers(coverage, ("window_days", "days_with_archive", "partial_days")),
                "per_metric_days": _numbers(coverage.get("per_metric_days"), (
                    "sleep_minutes", "sleep_efficiency", "resting_hr", "hrv_ms", "steps", "active_minutes", "sedentary_minutes",
                )),
                "excluded_out_of_range": {
                    ("body_fat_pct" if key == "body_fat" else key): metrics.to_float((coverage.get("excluded_out_of_range") or {}).get(key, 0))
                    for key in ("sleep_minutes", "sleep_efficiency", "resting_hr", "hrv_ms",
                                "spo2", "breathing_rate", "weight_kg", "body_fat")
                },
            },
            "correlations": correlations,
        },
    }


# 显式出口白名单；不从分析层的未来配对配置自动扩展。
CORRELATION_SLOTS = (
    ("sleep_minutes", "resting_hr"), ("sleep_minutes", "hrv_ms"),
    ("deep_minutes", "resting_hr"), ("sleep_efficiency", "hrv_ms"),
    ("sleep_mid", "resting_hr"), ("sleep_mid", "hrv_ms"),
    ("sleep_minutes", "steps"), ("steps", "sleep_minutes"),
    ("steps", "deep_minutes"), ("steps", "resting_hr"),
    ("active_minutes", "sleep_minutes"), ("sedentary_minutes", "resting_hr"),
)
DUAL_FIELDS = {
    ("sleep", "total_minutes"): "sleep_minutes",
    ("heart", "resting_hr"): "resting_hr", ("heart", "hrv_ms"): "hrv_ms",
    ("heart", "breathing_rate"): "breathing_rate", ("heart", "spo2"): "spo2",
    ("heart", "skin_temp_delta_c"): "skin_temp_delta",
}


def build_payload(day: dict, goals: dict, analysis: dict, insights: dict, *,
                  today: str | None = None, history: list[dict] | None = None) -> dict:
    """单日 daily-v2：当日指所选日；六项状态双值，行为值只有 *_today。"""
    payload = _legacy_payload(day, goals, analysis, insights, today=today)
    observations = {item["date"]: item for item in (history or []) if isinstance(item, dict) and "date" in item}
    observations[day["date"]] = day  # 当前日快照为所选日的权威读数。
    readings = [(selected, {key: metrics.to_float(value) for key, value in analysis_module.comparison_values(item).items()})
                for selected, item in observations.items()]
    averages = reliability.calendar_averages(readings, day["date"])
    daily = {key: metrics.to_float(value) for key, value in analysis_module.comparison_values(day).items()}

    def rename(value, path=()):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if isinstance(child, dict):
                    result[key] = rename(child, (*path, key))
                else:
                    result[key + "_today"] = child
                    metric = DUAL_FIELDS.get((*path, key))
                    if metric:
                        result[key + "_7d"] = averages[metric]
            return result
        return value

    payload["day"] = rename(payload["day"])
    stats = payload["statistics"]
    for key, item in stats["comparisons"].items():
        baseline = item["baseline"]
        values = {"today": daily[key]}
        if key in reliability.STATE_PRECISION:
            values["7d"] = averages[key]
        comparison = {name: item[name] for name in ("baseline", "sample_count", "window_days", "min_samples")}
        for basis, value in values.items():
            comparison["current_" + basis] = value
            comparison.update({name + "_" + basis: amount for name, amount in
                               analysis_module.baseline_change(value, baseline).items()})
        stats["comparisons"][key] = comparison
    stats["goal_progress"] = {key: {name + "_today": value for name, value in item.items()}
                              for key, item in stats["goal_progress"].items()}
    for name in ("hrv_delta_pct", "rhr_delta"):
        stats["recovery"][name + "_today"] = stats["recovery"].pop(name)
    approved = {(item["x"], item["y"]): item for item in stats["correlations"]}
    stats["correlations"] = {
        x + "__" + y: {"x": x, "y": y, **_numbers(approved.get((x, y)), ("offset_days", "r", "n", "ci_low", "ci_high"))}
        for x, y in CORRELATION_SLOTS
    }
    payload["window"] = build_window_payload([day], goals, day["date"], day["date"], today=today)["window"]
    payload["rolling_window_days"] = reliability.ROLLING_SIZE
    return payload


MAX_WINDOW_DAYS = 366


def normalize_window(date: str | None = None, *, start: str | None = None, end: str | None = None) -> tuple[str, str]:
    if date is not None:
        if start is not None or end is not None:
            raise AIError(t('server.ai.service.normalize_window.6315ca3b8'))
        start = end = date
    try:
        if not all(isinstance(item, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item) for item in (start, end)):
            raise ValueError
        first, last = date_type.fromisoformat(start), date_type.fromisoformat(end)
    except (ValueError, TypeError):
        raise AIError(t('server.ai.service.normalize_window.ff9ac5a86')) from None
    span = (last - first).days + 1
    if span < 1 or span > MAX_WINDOW_DAYS:
        raise AIError(t('server.ai.service.normalize_window.f56540c45', MAX_WINDOW_DAYS=MAX_WINDOW_DAYS))
    return start, end


def _relative_day(day: str, today: str) -> str:
    age = (date_type.fromisoformat(today) - date_type.fromisoformat(day)).days
    return f"D-{age}" if age >= 0 else f"D+{-age}"


def _missing_streak(values) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value is None else 0
        longest = max(longest, current)
    return longest


def build_window_payload(days: list[dict], goals: dict, start: str, end: str, *, today: str | None = None) -> dict:
    """先在本机做完整日历统计；逐日值只在本函数内存在，出口不含任何序列。"""
    start, end = normalize_window(start=start, end=end)
    today = today or iso_today()
    dates = iso_range(end, (date_type.fromisoformat(end) - date_type.fromisoformat(start)).days + 1)
    by_date = {day['date']: day for day in days if isinstance(day, dict) and isinstance(day.get('date'), str) and day['date'] in dates}
    # 复用既有单日出口白名单及权威派生活动分钟；不遍历原始对象的任意字段。
    approved = {day: _legacy_payload(value, goals, {}, {}, today=today)['day'] for day, value in by_date.items()}
    template = _legacy_payload({'date': start}, goals, {}, {}, today=today)['day']
    bounded = {
        ('sleep', 'total_minutes'): 'sleep_minutes', ('sleep', 'efficiency'): 'sleep_efficiency',
        ('heart', 'resting_hr'): 'resting_hr', ('heart', 'hrv_ms'): 'hrv_ms',
        ('heart', 'spo2'): 'spo2', ('heart', 'breathing_rate'): 'breathing_rate',
        ('body', 'weight_kg'): 'weight_kg', ('body', 'body_fat_pct'): 'body_fat',
    }
    measured_dates = set()
    series = {}

    def at(source, path):
        for key in path:
            source = source.get(key) if isinstance(source, dict) else None
        return metrics.to_float(source)

    def summarize(path):
        values, excluded = [], 0
        bounds = insights_module.BOUNDS.get(bounded.get(path))
        for day in dates:
            value = at(approved.get(day), path)
            if value is not None and bounds is not None and not bounds[0] <= value <= bounds[1]:
                value = None
                excluded += 1
            values.append(value)
            if value is not None:
                measured_dates.add(day)
        series[path] = values
        known = [(day, value) for day, value in zip(dates, values) if value is not None]
        low = min(known, key=lambda item: item[1]) if known else None
        high = max(known, key=lambda item: item[1]) if known else None
        first, last = (known[0], known[-1]) if known else (None, None)
        delta = last[1] - first[1] if len(known) >= 2 else None
        return {
            'mean': round(mean(value for _, value in known), 4) if known else None,
            'median': round(mean(sorted(value for _, value in known)[(len(known)-1)//2:len(known)//2+1]), 4) if known else None,
            'min': low[1] if low else None, 'max': high[1] if high else None,
            'min_day': _relative_day(low[0], today) if low else None,
            'max_day': _relative_day(high[0], today) if high else None,
            'coverage_days': len(known), 'missing_days': len(dates) - len(known),
            'max_missing_streak': _missing_streak(values), 'excluded_days': excluded,
            'trend': {
                'first': first[1] if first else None, 'last': last[1] if last else None,
                'direction': ('up' if delta > 0 else 'down' if delta < 0 else 'flat') if delta is not None else None,
            },
        }

    def walk(value, path=()):
        if isinstance(value, dict):
            return {key: walk(child, (*path, key)) for key, child in value.items()}
        return summarize(path)

    measurements = walk(template)
    achievement = {}
    for name, path in [('steps', ('activity', 'steps')), ('sleep_minutes', ('sleep', 'total_minutes'))]:
        goal = metrics.to_float(goals.get(name))
        known = [(day, value) for day, value in zip(dates, series[path]) if value is not None]
        ratios = [(day, min(1, max(0, value / goal))) for day, value in known] if goal is not None and goal > 0 else []
        best = max(ratios, key=lambda item: item[1]) if ratios else None
        worst = min(ratios, key=lambda item: item[1]) if ratios else None
        achieved = sum(value >= goal for _, value in known) if ratios else None
        achievement[name] = {
            'goal': goal, 'coverage_days': len(known), 'missing_days': len(dates) - len(known),
            'achieved_days': achieved,
            'achievement_rate': round(achieved / len(known), 4) if ratios else None,
            'best_day': _relative_day(best[0], today) if best else None,
            'worst_day': _relative_day(worst[0], today) if worst else None,
            'best_ratio': round(best[1], 4) if best else None, 'worst_ratio': round(worst[1], 4) if worst else None,
        }
    return {
        'window': {
            'days': len(dates), 'days_with_data': len(measured_dates), 'days_with_archive': len(approved),
            'missing_days': len(dates) - len(measured_dates),
            'max_missing_streak': _missing_streak([1 if day in measured_dates else None for day in dates]),
            'date_range': f"{_relative_day(start, today)} → {_relative_day(end, today)}",
        },
        'metrics': measurements, 'goals': achievement,
    }


def payload_audit(payload: dict) -> dict:
    """仅记录路径、形状与内容摘要，不复制健康值。数组索引归一为 []。"""
    shapes: dict[str, dict] = {}

    def walk(value, path: str) -> None:
        shape = {"type": "null" if value is None else type(value).__name__}
        if isinstance(value, (str, list)):
            shape["length"] = len(value)
        if path:
            previous = shapes.get(path)
            if previous and "length" in shape:
                shape["length"] = max(previous.get("length", 0), shape["length"])
            shapes[path] = shape
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for item in value:
                walk(item, path + "[]")

    walk(payload, "")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    keys = sorted(shapes)
    keys_hash = hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()
    return {"payload_hash": hashlib.sha256(encoded.encode()).hexdigest(), "payload_keys": keys,
            "payload_keys_hash": keys_hash, "payload_shapes": shapes}


class AIService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.secrets = SecretStore(cfg.secret_key_path)
        self._lock = threading.Lock()

    def public_settings(self, conn) -> dict:
        settings = storage.load_settings(conn)
        return {
            "enabled": bool(settings["ai_enabled"]), "base_url": validate_base_url(settings["ai_base_url"]),
            "model": settings["ai_model"], "has_api_key": bool(storage.load_credentials_raw(conn, "ai")),
            "prompt_version": prompt.PROMPT_VERSION, "window_prompt_version": prompt.WINDOW_PROMPT_VERSION,
            "max_window_days": MAX_WINDOW_DAYS,
            "payload_schema_version": schema.DAILY_SCHEMA_VERSION, "window_payload_schema_version": schema.WINDOW_SCHEMA_VERSION,
        }

    def configure(self, conn, patch: dict) -> dict:
        current = storage.load_settings(conn)
        enabled = patch.get("ai_enabled", current["ai_enabled"])
        if not isinstance(enabled, bool):
            raise AIError(t('server.ai.service.configure.1a354282c'))
        base_url = patch.get("ai_base_url", current["ai_base_url"])
        model = patch.get("ai_model", current["ai_model"])
        if not isinstance(base_url, str) or not isinstance(model, str):
            raise AIError(t('server.ai.service.configure.0ca8cba02'))
        base_url = validate_base_url(base_url)
        model = model.strip()
        if len(model) > 200 or any(ord(char) < 32 for char in model) or (enabled and not model):
            raise AIError(t('server.ai.service.configure.57dd34c6c'))
        key = patch.get("api_key", "")
        clear = patch.get("clear_api_key", False)
        if not isinstance(key, str) or not isinstance(clear, bool) or (clear and key.strip()):
            raise AIError(t('server.ai.service.configure.83740ba43'))
        if any(ord(char) < 33 or ord(char) > 126 for char in key.strip()):
            raise AIError(t('server.ai.service.configure.81a79eaeb'))
        encrypted = self.secrets.encrypt_json({"api_key": key.strip()}) if key.strip() else None
        with conn:
            conn.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", [
                ("ai_enabled", json.dumps(enabled)), ("ai_base_url", json.dumps(base_url)),
                ("ai_model", json.dumps(model)),
            ])
            if clear:
                conn.execute("DELETE FROM credentials WHERE provider = 'ai'")
            elif encrypted:
                conn.execute("INSERT OR REPLACE INTO credentials (provider, data, updated_at) VALUES ('ai', ?, ?)",
                             (encrypted, utc_now_iso()))
        return self.public_settings(conn)

    def _client(self, conn, settings: dict, *, start_date: str | None = None, end_date: str | None = None, require_model: bool = True) -> AIClient:
        if require_model and not settings["ai_model"].strip():
            raise AIError(AI_ERROR_TEXT["missing_model"])
        raw_key = storage.load_credentials_raw(conn, "ai")
        try:
            key = (self.secrets.decrypt_json(raw_key).get("api_key") or "") if raw_key else ""
            if not isinstance(key, str):
                raise TypeError
        except (SecretError, ValueError, TypeError, AttributeError):
            raise AIError(t('server.ai.service._client.39b84df62')) from None
        base_url = validate_base_url(settings["ai_base_url"])
        session_id = None
        if start_date is not None:
            row = conn.execute(
                "SELECT e.session_id FROM ai_egress e JOIN ai_analyses a ON a.id = e.analysis_id "
                "WHERE a.start_date = ? AND a.end_date = ? AND a.model = ? AND a.base_url = ? AND e.session_id IS NOT NULL "
                "ORDER BY e.id DESC LIMIT 1", (start_date, end_date, settings["ai_model"], base_url),
            ).fetchone()
            session_id = row["session_id"] if row else None
        return AIClient(base_url, settings["ai_model"], key, session_id=session_id)

    def list_models(self) -> dict:
        try:
            with closing(db.connect(self.cfg.db_path)) as conn:
                client = self._client(conn, storage.load_settings(conn), require_model=False)
            return client.list_models()
        except AIError as exc:
            return {"ok": False, "status": exc.kind or exc.status, "detail": str(exc), "models": []}

    def test_connection(self) -> dict:
        started = time.perf_counter()
        model = ""
        try:
            with closing(db.connect(self.cfg.db_path)) as conn:
                settings = storage.load_settings(conn)
                model = settings["ai_model"]
                client = self._client(conn, settings)
            return client.test_connection()
        except AIError as exc:
            return {"ok": False, "status": exc.kind or exc.status, "detail": str(exc), "model": model,
                    "latency_ms": round((time.perf_counter() - started) * 1000)}

    @staticmethod
    def _result(conn, row) -> dict:
        egress = [dict(item) for item in conn.execute("SELECT * FROM ai_egress WHERE analysis_id = ? ORDER BY attempt", (row["id"],))]
        first = egress[0]
        validation = json.loads(row["validation_json"])
        return {
            "date": row["date"], "start_date": row["start_date"] or row["date"],
            "end_date": row["end_date"] or row["date"], "model": row["model"], "prompt_version": row["prompt_version"],
            "payload_schema_version": row["payload_schema_version"],
            "content": json.loads(row["content"]), "raw_content": row["raw_content"],
            "validation": validation, "finish_reason": validation.get("finish_reason"),
            "truncated": bool(validation.get("truncated")), "usage": json.loads(row["usage_json"]),
            "created_at": row["created_at"], "payload_hash": first["payload_hash"],
            "payload_keys": json.loads(first["payload_keys_json"]), "attempts": len(egress), "cached": True,
        }

    @classmethod
    def _cached(cls, conn, start: str, end: str, settings: dict) -> dict | None:
        row = conn.execute(
            "SELECT * FROM ai_analyses WHERE start_date = ? AND end_date = ? AND model = ? AND prompt_version = ? "
            "AND payload_schema_version = ? AND base_url = ? AND status = 'ok' ORDER BY id DESC LIMIT 1",
            (start, end, settings["ai_model"], prompt.version_for_window(start, end),
             schema.version_for_window(start, end), validate_base_url(settings["ai_base_url"])),
        ).fetchone()
        if row is None:
            return None
        return cls._result(conn, row)

    def get(self, date: str | None = None, *, start: str | None = None, end: str | None = None) -> dict:
        start, end = normalize_window(date, start=start, end=end)
        with closing(db.connect(self.cfg.db_path)) as conn:
            return {"analysis": self._cached(conn, start, end, storage.load_settings(conn)), "settings": self.public_settings(conn)}

    def analyze(self, date: str | None = None, *, start: str | None = None, end: str | None = None, force: bool = False) -> dict:
        start, end = normalize_window(date, start=start, end=end)
        if not self._lock.acquire(blocking=False):
            raise AIError(t('server.ai.service.analyze.7bb1c3527'), 409)
        try:
            with closing(db.connect(self.cfg.db_path)) as conn:
                settings = storage.load_settings(conn)
                if not settings["ai_enabled"]:
                    raise AIError(t('server.ai.service.analyze.6c67281f1'))
                if not force and (cached := self._cached(conn, start, end, settings)) is not None:
                    return cached
                span = (date_type.fromisoformat(end) - date_type.fromisoformat(start)).days + 1
                days = storage.get_day_window(conn, end, span)
                if not days:
                    raise AIError(t('server.ai.service.analyze.ba27bfcba'), 404)
                client = self._client(conn, settings, start_date=start, end_date=end)
                goals = settings.get("goals") or {}
                if start == end:
                    day = storage.get_day(conn, end)
                    history = storage.get_day_window(conn, end, 30)
                    trends = storage.get_trends(conn, end, metrics.BASELINE_WINDOW_DAYS + 1)
                    payload = build_payload(day, goals, analysis_module.build_analysis(day, trends, goals),
                                            insights_module.build_insights(history, goals, end_date=end, window_days=30),
                                            history=history)
                else:
                    payload = build_window_payload(days, goals, start, end)
                audit = payload_audit(payload)
                with conn:
                    analysis_id = conn.execute(
                        "INSERT INTO ai_analyses (date, start_date, end_date, model, prompt_version, payload_schema_version, created_at, base_url, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                        (end, start, end, settings["ai_model"], prompt.version_for_window(start, end),
                         schema.version_for_window(start, end), utc_now_iso(), client.base_url),
                    ).lastrowid
            messages = prompt.build_messages(payload)
            text = ""
            usages = []
            finish_reasons = []
            for attempt in (1, 2):
                echo = None if attempt == 1 else {
                    "kind": "model_generated_text", "length": len(text),
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
                request_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                with closing(db.connect(self.cfg.db_path)) as conn, conn:
                    if conn.execute("SELECT id FROM ai_analyses WHERE id = ?", (analysis_id,)).fetchone() is None:
                        raise AIError(t('server.ai.service.analyze.5917e9fe9'), 409)
                    egress_id = conn.execute(
                        "INSERT INTO ai_egress (analysis_id, attempt, payload_keys_json, payload_keys_hash, payload_hash, "
                        "payload_shapes_json, request_hash, assistant_echo_json, status, sent_at, session_id, user_agent, payload_schema_version) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                        (analysis_id, attempt, json.dumps(audit["payload_keys"]), audit["payload_keys_hash"],
                         audit["payload_hash"], json.dumps(audit["payload_shapes"]), request_hash,
                         json.dumps(echo) if echo else None, utc_now_iso(), client.session_id, USER_AGENT,
                         schema.version_for_window(start, end)),
                    ).lastrowid
                # 网络等待期间不持有 SQLite 事务；每个实际请求都有独立留痕。
                try:
                    response_text, usage, finish_reason = client.complete(messages)
                except AIError as exc:
                    finish_reasons.append(None)
                    with closing(db.connect(self.cfg.db_path)) as conn, conn:
                        conn.execute("UPDATE ai_egress SET status = 'error' WHERE id = ?", (egress_id,))
                        if attempt == 1:
                            conn.execute("UPDATE ai_analyses SET status = 'error' WHERE id = ?", (analysis_id,))
                    if attempt == 1:
                        raise
                    validation["state"] = "failed"
                    validation["issues"].append({"section": "retry", "text": "", "reason": t('server.ai.service.analyze.3f32ad0e9', p1=str(exc))})
                    usages.append({key: None for key in ("prompt_tokens", "completion_tokens", "total_tokens")})
                    break  # 保留上一轮原文，不能因重试网络失败把它吞掉。
                finish_reasons.append(finish_reason)
                text = response_text
                usages.append(usage)
                with closing(db.connect(self.cfg.db_path)) as conn, conn:
                    conn.execute("UPDATE ai_egress SET status = 'ok', usage_json = ? WHERE id = ?", (json.dumps(usage), egress_id))
                content, validation = validate_output(text, payload)
                validation.update(finish_reason=finish_reason, truncated=finish_reason == "length", max_tokens=ANALYSIS_MAX_TOKENS)
                if finish_reason == "length":
                    validation["state"] = "failed"
                    validation["issues"] = [{"section": "output", "text": "", "reason":
                        t('server.ai.service.analyze.d1f7108f6', ANALYSIS_MAX_TOKENS=ANALYSIS_MAX_TOKENS)}]
                    validation["warnings"] = []
                if validation["state"] != "failed" or attempt == 2:
                    break
                with using_language("zh", group_numbers=False):
                    messages += [
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": prompt.build_repair_message([
                            t('server.ai.service.analyze.12d0a887b', p1=item['section'], p2=item['reason'], p3=item['text']) for item in validation["issues"]
                        ])},
                    ]
            validation["attempt_finish_reasons"] = finish_reasons
            validated_at = utc_now_iso()
            validation.update(rules_version=RULES_VERSION, validated_at=validated_at)
            total_usage = {
                key: sum(item[key] for item in usages) if all(item.get(key) is not None for item in usages) else None
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            with closing(db.connect(self.cfg.db_path)) as conn, conn:
                conn.execute(
                    "UPDATE ai_analyses SET content = ?, raw_content = ?, validation_state = ?, validation_json = ?, "
                    "usage_json = ?, status = 'ok', created_at = ? WHERE id = ?",
                    (json.dumps(content, ensure_ascii=False), text, validation["state"], json.dumps(validation, ensure_ascii=False),
                     json.dumps(total_usage), validated_at, analysis_id),
                )
                row = conn.execute("SELECT * FROM ai_analyses WHERE id = ?", (analysis_id,)).fetchone()
                if row is None:
                    raise AIError(t('server.ai.service.analyze.5917e9fe9'), 409)
                result = self._result(conn, row)
            return {**result, "cached": False}
        finally:
            self._lock.release()
