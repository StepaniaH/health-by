"""Fitbit 手动导出 / Google Takeout ZIP 与解压目录离线导入器。

安全约束：
- 仅在内存中读取 ZIP 成员，绝不向磁盘解包；
- 拒绝路径穿越（`..`、绝对路径、反斜杠、盘符）、符号链接，并限制成员数与解压字节数。
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import stat
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from health_by import storage
from health_by.i18n import t
from health_by.models import (
    SLEEP_STAGES,
    ActivityDay,
    BodyDay,
    Coverage,
    DaySnapshot,
    HeartDay,
    SleepSession,
    StageSegment,
    TimePoint,
    Workout,
)
from health_by.providers.base import RawDay
from health_by.util import utc_now_iso

MAX_MEMBERS = 20_000
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
LB_TO_KG = 0.45359237
EXPORT_SOURCE = "fitbit-export"

FitbitExportStats = dict[str, Any]

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_STAGE_MAP = {
    "deep": "deep",
    "light": "light",
    "rem": "rem",
    "wake": "wake",
    "asleep": "light",
    "restless": "wake",
    "awake": "wake",
}

_CSV_REQUIRED_COLUMN_GROUPS: dict[str, tuple[frozenset[str], ...]] = {
    "hrv_csv": (
        frozenset({"timestamp", "date"}),
        frozenset({"rmssd"}),
    ),
    "spo2_csv": (
        frozenset({"timestamp", "date"}),
        frozenset({"average_value", "value"}),
    ),
    "breathing_csv": (
        frozenset({"timestamp", "date"}),
        frozenset({"daily_respiratory_rate", "full_sleep_breathing_rate"}),
    ),
    "skin_temp_csv": (
        frozenset({"sleep_end", "timestamp", "date", "sleep_start"}),
        frozenset({"nightly_temperature", "baseline_relative_sample_sum"}),
    ),
}


def _has_required_csv_headers(kind: str, fieldnames: list[str] | None) -> bool:
    """校验各类 Fitbit CSV 至少包含日期列及对应主指标列的必需表头，防止截断在表头中间时被当成空表。"""
    if not fieldnames:
        return False
    headers = {str(name).strip().lower() for name in fieldnames if name is not None and str(name).strip()}
    required_groups = _CSV_REQUIRED_COLUMN_GROUPS.get(kind, ())
    return all(bool(headers & group) for group in required_groups)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _parse_dt(value: Any) -> tuple[str | None, str | None, str | None]:
    """解析 Fitbit JSON / CSV 中的常见日期时间，返回 (YYYY-MM-DD, HH:MM|None, ISO|None)。"""
    if not isinstance(value, str):
        return None, None, None
    text = value.strip()
    if not text:
        return None, None, None

    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            dt = datetime.strptime(text, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d"), None, None
        except ValueError:
            return None, None, None

    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_candidate)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), dt.isoformat(timespec="seconds")
    except ValueError:
        pass

    for fmt, has_time in (
        ("%m/%d/%y %H:%M:%S", True),
        ("%m/%d/%Y %H:%M:%S", True),
        ("%m/%d/%y %H:%M", True),
        ("%m/%d/%Y %H:%M", True),
        ("%m/%d/%y", False),
        ("%m/%d/%Y", False),
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return (
                dt.strftime("%Y-%m-%d"),
                dt.strftime("%H:%M") if has_time else None,
                dt.isoformat(timespec="seconds") if has_time else None,
            )
        except ValueError:
            continue
    return None, None, None


def _validate_zip_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if "\\" in name or _DRIVE_RE.match(name) or name.startswith("/"):
        raise ValueError(t("server.importers.fitbit_zip.unsafe_path", name=name))
    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        raise ValueError(t("server.importers.fitbit_zip.unsafe_path", name=name))
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ValueError(t("server.importers.fitbit_zip.symlink_rejected", name=name))
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError(t("server.importers.fitbit_zip.member_too_large", name=name))


def _read_zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, total_read: int) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    size = 0
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            size += len(chunk)
            total_read += len(chunk)
            if size > MAX_MEMBER_BYTES:
                raise ValueError(t("server.importers.fitbit_zip.member_too_large", name=info.filename))
            if total_read > MAX_TOTAL_BYTES:
                raise ValueError(t("server.importers.fitbit_zip.archive_too_large"))
            chunks.append(chunk)
    return b"".join(chunks), total_read


def _classify_filename(name: str) -> tuple[str, str] | None:
    lower = Path(name).name.lower()
    if lower.endswith(".json"):
        for prefix, kind in (
            ("steps-", "steps"),
            ("calories-", "calories"),
            ("distance-", "distance"),
            ("resting_heart_rate-", "resting_hr"),
            ("sleep-", "sleep"),
            ("weight-", "weight"),
            ("exercise-", "exercise"),
        ):
            if lower.startswith(prefix):
                return kind, "json"
        return None
    if lower.endswith(".csv"):
        if lower.startswith("daily heart rate variability summary") or lower.startswith("heart_rate_variability"):
            return "hrv_csv", "csv"
        if lower.startswith("daily spo2") or lower.startswith("minute spo2"):
            return "spo2_csv", "csv"
        if lower.startswith("daily respiratory rate summary") or lower.startswith("respiratory rate summary"):
            return "breathing_csv", "csv"
        if lower.startswith("computed temperature"):
            return "skin_temp_csv", "csv"
    return None


def _iter_recognized_files(source: Path):
    """安全迭代 ZIP 或目录中可识别的 Fitbit 导出文件（名、种类、格式、字节内容）。"""
    source = Path(source)
    if zipfile.is_zipfile(source):
        total_read = 0
        member_count = 0
        with zipfile.ZipFile(source, "r") as zf:
            for info in zf.infolist():
                _validate_zip_member(info)
                if info.is_dir():
                    continue
                member_count += 1
                if member_count > MAX_MEMBERS:
                    raise ValueError(t("server.importers.fitbit_zip.too_many_members"))
                classified = _classify_filename(info.filename)
                if classified is None:
                    continue
                data, total_read = _read_zip_member(zf, info, total_read)
                yield info.filename, classified[0], classified[1], data
        return

    if source.is_dir():
        root = source.resolve()
        member_count = 0
        total_read = 0
        for entry in sorted(root.rglob("*")):
            if entry.is_symlink() or not entry.is_file():
                continue
            resolved = entry.resolve()
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(t("server.importers.fitbit_zip.unsafe_path", name=str(entry))) from exc
            member_count += 1
            if member_count > MAX_MEMBERS:
                raise ValueError(t("server.importers.fitbit_zip.too_many_members"))
            classified = _classify_filename(rel)
            if classified is None:
                continue
            size = resolved.stat().st_size
            if size > MAX_MEMBER_BYTES:
                raise ValueError(t("server.importers.fitbit_zip.member_too_large", name=rel))
            total_read += size
            if total_read > MAX_TOTAL_BYTES:
                raise ValueError(t("server.importers.fitbit_zip.archive_too_large"))
            yield rel, classified[0], classified[1], resolved.read_bytes()
        return

    raise ValueError(t("server.importers.fitbit_zip.unsupported_source", path=str(source)))


def _is_int_like(raw_val: Any) -> bool:
    if isinstance(raw_val, bool):
        return False
    if isinstance(raw_val, int):
        return True
    text = str(raw_val).strip()
    return text.isdigit()


def _parse_sleep_entry(entry: dict[str, Any]) -> tuple[str | None, bool, SleepSession]:
    day = entry.get("dateOfSleep")
    if isinstance(day, str):
        parsed_day, _, _ = _parse_dt(day)
        day = parsed_day
    if not day:
        parsed_day, _, _ = _parse_dt(entry.get("endTime") or entry.get("startTime"))
        day = parsed_day
    _, _, start_iso = _parse_dt(entry.get("startTime"))
    _, _, end_iso = _parse_dt(entry.get("endTime"))

    def opt_int(val: Any) -> int | None:
        num = _safe_float(val)
        return None if num is None else int(round(num))

    stages: dict[str, int] = {}
    timeline: list[StageSegment] = []
    levels = entry.get("levels")
    if isinstance(levels, dict):
        summary = levels.get("summary")
        if isinstance(summary, dict):
            for stage_name in SLEEP_STAGES:
                item = summary.get(stage_name)
                if isinstance(item, dict) and item.get("minutes") is not None:
                    mins = opt_int(item.get("minutes"))
                    if mins is not None:
                        stages[stage_name] = mins
        data_rows = levels.get("data")
        if isinstance(data_rows, list):
            for seg in data_rows:
                if not isinstance(seg, dict):
                    continue
                mapped = _STAGE_MAP.get(str(seg.get("level") or "").lower())
                _, _, seg_start = _parse_dt(seg.get("dateTime"))
                secs = _safe_float(seg.get("seconds"))
                if not mapped or not seg_start or secs is None or secs <= 0:
                    continue
                try:
                    start_dt = datetime.fromisoformat(seg_start)
                    from datetime import timedelta

                    seg_end = (start_dt + timedelta(seconds=secs)).isoformat(timespec="seconds")
                    timeline.append(StageSegment(start=seg_start, end=seg_end, stage=mapped))
                except ValueError:
                    continue

    total_minutes = opt_int(entry.get("minutesAsleep"))
    is_main = bool(entry.get("mainSleep", True))
    session = SleepSession(
        is_main=is_main,
        start_time=start_iso or (str(entry.get("startTime")) if entry.get("startTime") else None),
        end_time=end_iso or (str(entry.get("endTime")) if entry.get("endTime") else None),
        total_minutes=total_minutes,
        time_in_bed_minutes=opt_int(entry.get("timeInBed")),
        efficiency=opt_int(entry.get("efficiency")),
        minutes_awake=opt_int(entry.get("minutesAwake")),
        minutes_to_fall_asleep=opt_int(entry.get("minutesToFallAsleep")),
        minutes_after_wake_up=opt_int(entry.get("minutesAfterWakeup")),
        stages=stages,
        stage_timeline=timeline,
    )
    return day, is_main, session


def _normalize_distance_km(
    raw_values: list[tuple[Any, float, str]],
    *,
    distance_unit: str = "auto",
) -> float | None:
    """将 Fitbit `distance-*.json` 条目换算为公里。

    Fitbit Global Export 的 `distance-*.json` 以厘米整数字符串存储（例如 `"999"` 表示 9.99 米，
    `"100000"` 表示 1.00 公里）。不再按 `total >= 1000` 猜测单位，避免在 999→1000 处发生量级突变：
    - 显式指定 `distance_unit`（非 `auto`）或条目自带 `unit` 时严格按单位换算；
    - `auto` 下：纯整数形式一律按 Fitbit 标准厘米（÷100,000）换算；含小数点的数值按公里处理。
    """
    if not raw_values:
        return None
    if distance_unit != "auto":
        total = sum(num for _, num, _ in raw_values)
        return _convert_distance_to_km(total, distance_unit)

    explicit_units = {unit for _, _, unit in raw_values if unit}
    if explicit_units:
        total_km = 0.0
        for raw, num, unit in raw_values:
            effective = unit or ("cm" if _is_int_like(raw) else "km")
            total_km += _convert_distance_to_km(num, effective, round_result=False)
        return round(total_km, 2)

    total = sum(num for _, num, _ in raw_values)
    if all(_is_int_like(raw) for raw, _, _ in raw_values):
        return round(total / 100_000.0, 2)
    return round(total, 2)


def _convert_distance_to_km(value: float, unit: str, *, round_result: bool = True) -> float:
    norm = unit.strip().lower()
    if norm in ("cm", "centimeter", "centimeters"):
        km = value / 100_000.0
    elif norm in ("m", "meter", "meters"):
        km = value / 1000.0
    elif norm in ("mi", "mile", "miles"):
        km = value * 1.60934
    else:
        km = value
    return round(km, 2) if round_result else km


def _workout_distance_km(entry: dict[str, Any]) -> float | None:
    dist = _safe_float(entry.get("distance"))
    if dist is None:
        return None
    unit = str(entry.get("distanceUnit") or "").lower()
    if unit in ("mile", "miles", "mi"):
        return round(dist * 1.60934, 2)
    if unit in ("meter", "meters", "m"):
        return round(dist / 1000.0, 2)
    return round(dist, 2)


def parse_fitbit_export(
    source: Path,
    *,
    weight_unit: str = "lb",
    distance_unit: str = "auto",
    file_counts: dict[str, Any] | None = None,
) -> tuple[list[tuple[DaySnapshot, RawDay]], int]:
    """解析 Fitbit ZIP 或导出目录为 (DaySnapshot, RawDay) 列表，同时返回成功解析的文件数。"""
    generated_at = utc_now_iso()
    per_day_raw: dict[str, dict[str, Any]] = defaultdict(dict)
    sources_per_day: dict[str, set[str]] = defaultdict(set)

    steps_sum: dict[str, float] = defaultdict(float)
    steps_intraday: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    steps_has_subday: dict[str, bool] = defaultdict(bool)
    calories_sum: dict[str, float] = defaultdict(float)
    distance_items: dict[str, list[tuple[Any, float, str]]] = defaultdict(list)
    resting_hrs: dict[str, list[float]] = defaultdict(list)
    sleeps: dict[str, list[tuple[bool, SleepSession]]] = defaultdict(list)
    weights: dict[str, float] = {}
    body_fats: dict[str, float] = {}
    workouts_by_day: dict[str, list[Workout]] = defaultdict(list)
    hrv_values: dict[str, list[float]] = defaultdict(list)
    spo2_avg: dict[str, list[float]] = defaultdict(list)
    spo2_min: dict[str, list[float]] = defaultdict(list)
    spo2_max: dict[str, list[float]] = defaultdict(list)
    breathing_values: dict[str, list[float]] = defaultdict(list)
    skin_temp_nightly: dict[str, list[float]] = defaultdict(list)
    skin_temp_delta: dict[str, list[float]] = defaultdict(list)

    files_recognized = 0
    files_parsed = 0
    files_failed = 0
    failed_files: list[str] = []
    for rel_name, kind, fmt, content in _iter_recognized_files(source):
        files_recognized += 1
        text = content.decode("utf-8-sig", errors="replace")
        if fmt == "json":
            try:
                loaded = json.loads(text)
            except ValueError:
                files_failed += 1
                failed_files.append(rel_name)
                continue
            if not isinstance(loaded, list):
                files_failed += 1
                failed_files.append(rel_name)
                continue
            files_parsed += 1

            if kind == "steps":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, hhmm, _ = _parse_dt(item.get("dateTime"))
                    val = _safe_float(item.get("value"))
                    if not day or val is None:
                        continue
                    steps_sum[day] += val
                    sources_per_day[day].add("steps")
                    per_day_raw[day].setdefault("steps", []).append(item)
                    if hhmm:
                        steps_intraday[day][hhmm] += val
                        if hhmm != "00:00":
                            steps_has_subday[day] = True

            elif kind == "calories":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, _, _ = _parse_dt(item.get("dateTime"))
                    val = _safe_float(item.get("value"))
                    if not day or val is None:
                        continue
                    calories_sum[day] += val
                    sources_per_day[day].add("calories")
                    per_day_raw[day].setdefault("calories", []).append(item)

            elif kind == "distance":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, _, _ = _parse_dt(item.get("dateTime"))
                    raw_val = item.get("value")
                    val = _safe_float(raw_val)
                    if not day or val is None:
                        continue
                    unit = str(item.get("unit") or item.get("distanceUnit") or "").strip().lower()
                    distance_items[day].append((raw_val, val, unit))
                    sources_per_day[day].add("distance")
                    per_day_raw[day].setdefault("distance", []).append(item)

            elif kind == "resting_hr":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, _, _ = _parse_dt(item.get("dateTime"))
                    nested = item.get("value")
                    val = _safe_float(nested.get("value") if isinstance(nested, dict) else nested)
                    if not day or val is None or val <= 0:
                        continue
                    resting_hrs[day].append(val)
                    sources_per_day[day].add("resting_hr")
                    per_day_raw[day].setdefault("resting_hr", []).append(item)

            elif kind == "sleep":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, is_main, session = _parse_sleep_entry(item)
                    if not day:
                        continue
                    sleeps[day].append((is_main, session))
                    sources_per_day[day].add("sleep")
                    per_day_raw[day].setdefault("sleep", []).append(item)

            elif kind == "weight":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, _, _ = _parse_dt(item.get("date"))
                    if not day:
                        continue
                    kg = _safe_float(item.get("weightKg"))
                    if kg is None:
                        w = _safe_float(item.get("weight"))
                        if w is not None:
                            unit = str(item.get("unit") or "").lower()
                            kg = w if (unit == "kg" or (unit == "" and weight_unit == "kg")) else w * LB_TO_KG
                    if kg is not None and kg > 0:
                        weights[day] = round(kg, 1)
                    fat = _safe_float(item.get("fat"))
                    if fat is not None and fat > 0:
                        body_fats[day] = round(fat, 1)
                    if kg is not None or fat is not None:
                        sources_per_day[day].add("weight")
                        per_day_raw[day].setdefault("weight", []).append(item)

            elif kind == "exercise":
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    day, _, start_iso = _parse_dt(item.get("startTime"))
                    if not day:
                        continue
                    dur_ms = _safe_float(item.get("activeDuration"))
                    if dur_ms is None:
                        raw_dur = _safe_float(item.get("duration"))
                        dur_mins = round(raw_dur / 60_000.0, 1) if (raw_dur is not None and raw_dur > 1000) else raw_dur
                    else:
                        dur_mins = round(dur_ms / 60_000.0, 1)
                    cals = _safe_float(item.get("calories"))
                    avg_hr = _safe_float(item.get("averageHeartRate"))
                    steps_val = _safe_float(item.get("steps"))
                    workouts_by_day[day].append(
                        Workout(
                            name=str(item.get("activityName") or item.get("name") or "Workout"),
                            start_time=start_iso or item.get("startTime"),
                            duration_minutes=dur_mins,
                            calories=round(cals, 1) if cals is not None else None,
                            distance_km=_workout_distance_km(item),
                            average_hr=round(avg_hr, 1) if avg_hr is not None else None,
                            steps=round(steps_val) if steps_val is not None else None,
                        )
                    )
                    sources_per_day[day].add("exercise")
                    per_day_raw[day].setdefault("exercise", []).append(item)

        elif fmt == "csv":
            try:
                reader = csv.DictReader(io.StringIO(text), strict=True)
                csv_rows = list(reader)
            except csv.Error:
                files_failed += 1
                failed_files.append(rel_name)
                continue
            if not _has_required_csv_headers(kind, reader.fieldnames) or any(
                (None in row) or any(value is None for value in row.values())
                for row in csv_rows
            ):
                files_failed += 1
                failed_files.append(rel_name)
                continue
            files_parsed += 1
            for row in csv_rows:
                norm = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
                if kind == "hrv_csv":
                    day, _, _ = _parse_dt(norm.get("timestamp") or norm.get("date"))
                    rmssd = _safe_float(norm.get("rmssd"))
                    if day and rmssd is not None and rmssd > 0:
                        hrv_values[day].append(rmssd)
                        sources_per_day[day].add("hrv")
                        per_day_raw[day].setdefault("hrv_csv", []).append(dict(row))
                elif kind == "spo2_csv":
                    day, _, _ = _parse_dt(norm.get("timestamp") or norm.get("date"))
                    avg_v = _safe_float(norm.get("average_value") or norm.get("value"))
                    lo_v = _safe_float(norm.get("lower_bound"))
                    hi_v = _safe_float(norm.get("upper_bound"))
                    if day and avg_v is not None and avg_v > 0:
                        spo2_avg[day].append(avg_v)
                        if lo_v is not None:
                            spo2_min[day].append(lo_v)
                        if hi_v is not None:
                            spo2_max[day].append(hi_v)
                        sources_per_day[day].add("spo2")
                        per_day_raw[day].setdefault("spo2_csv", []).append(dict(row))
                elif kind == "breathing_csv":
                    day, _, _ = _parse_dt(norm.get("timestamp") or norm.get("date"))
                    br = _safe_float(norm.get("daily_respiratory_rate") or norm.get("full_sleep_breathing_rate"))
                    if day and br is not None and br > 0:
                        breathing_values[day].append(br)
                        sources_per_day[day].add("breathing")
                        per_day_raw[day].setdefault("breathing_csv", []).append(dict(row))
                elif kind == "skin_temp_csv":
                    day, _, _ = _parse_dt(
                        norm.get("sleep_end") or norm.get("timestamp") or norm.get("date") or norm.get("sleep_start")
                    )
                    nightly = _safe_float(norm.get("nightly_temperature"))
                    delta = _safe_float(norm.get("baseline_relative_sample_sum"))
                    if day and (nightly is not None or delta is not None):
                        if nightly is not None:
                            skin_temp_nightly[day].append(nightly)
                        if delta is not None:
                            skin_temp_delta[day].append(delta)
                        sources_per_day[day].add("skin_temp")
                        per_day_raw[day].setdefault("skin_temp_csv", []).append(dict(row))

    all_days = sorted(sources_per_day)
    snapshots: list[tuple[DaySnapshot, RawDay]] = []
    for day_iso in all_days:
        intraday_pts: list[TimePoint] = []
        if steps_has_subday.get(day_iso) and steps_intraday.get(day_iso):
            intraday_pts = [
                TimePoint(time=hhmm, value=round(val, 2))
                for hhmm, val in sorted(steps_intraday[day_iso].items())
            ]
        activity = ActivityDay(
            steps=int(round(steps_sum[day_iso])) if day_iso in steps_sum else None,
            calories_out=round(calories_sum[day_iso], 1) if day_iso in calories_sum else None,
            distance_km=(
                _normalize_distance_km(distance_items[day_iso], distance_unit=distance_unit)
                if day_iso in distance_items
                else None
            ),
            steps_intraday=intraday_pts,
        )

        rhr_list = resting_hrs.get(day_iso) or []
        hrv_list = hrv_values.get(day_iso) or []
        spo2_list = spo2_avg.get(day_iso) or []
        br_list = breathing_values.get(day_iso) or []
        temp_n_list = skin_temp_nightly.get(day_iso) or []
        temp_d_list = skin_temp_delta.get(day_iso) or []
        heart = HeartDay(
            resting_hr=int(round(sum(rhr_list) / len(rhr_list))) if rhr_list else None,
            hrv_ms=round(sum(hrv_list) / len(hrv_list), 1) if hrv_list else None,
            spo2=round(sum(spo2_list) / len(spo2_list), 1) if spo2_list else None,
            spo2_min=round(min(spo2_min[day_iso]), 1) if spo2_min.get(day_iso) else None,
            spo2_max=round(max(spo2_max[day_iso]), 1) if spo2_max.get(day_iso) else None,
            breathing_rate=round(sum(br_list) / len(br_list), 1) if br_list else None,
            skin_temp_nightly_c=round(sum(temp_n_list) / len(temp_n_list), 2) if temp_n_list else None,
            skin_temp_delta_c=round(sum(temp_d_list) / len(temp_d_list), 2) if temp_d_list else None,
        )

        main_sleep: SleepSession | None = None
        naps_minutes: int | None = None
        naps_count: int | None = None
        day_sleeps = sleeps.get(day_iso) or []
        if day_sleeps:
            ordered = sorted(
                enumerate(day_sleeps),
                key=lambda item: (item[1][0], item[1][1].total_minutes or -1, item[1][1].time_in_bed_minutes or -1, -item[0]),
                reverse=True,
            )
            main_sleep = ordered[0][1][1]
            main_sleep.is_main = True
            other_sleeps = [sess for _, (_, sess) in ordered[1:]]
            if other_sleeps:
                naps_count = len(other_sleeps)
                mins_list = [s.total_minutes for s in other_sleeps if s.total_minutes is not None]
                naps_minutes = sum(mins_list) if mins_list else None

        body = BodyDay(
            weight_kg=weights.get(day_iso),
            body_fat_pct=body_fats.get(day_iso),
        )
        src_count = len(sources_per_day[day_iso])
        snapshot = DaySnapshot(
            date=day_iso,
            source=EXPORT_SOURCE,
            generated_at=generated_at,
            activity=activity,
            heart=heart,
            sleep=main_sleep,
            naps_minutes=naps_minutes,
            naps_count=naps_count,
            body=body,
            workouts=workouts_by_day.get(day_iso, []),
            coverage=Coverage(endpoint_count=src_count, success_count=src_count),
        )
        raw_day = RawDay(
            date=day_iso,
            source=EXPORT_SOURCE,
            generated_at=generated_at,
            payload=dict(per_day_raw[day_iso]),
        )
        snapshots.append((snapshot, raw_day))
    if file_counts is not None:
        file_counts["files_recognized"] = files_recognized
        file_counts["files_parsed"] = files_parsed
        file_counts["files_failed"] = files_failed
        file_counts["failed_files"] = failed_files
    return snapshots, files_parsed


def import_fitbit_export(
    conn: sqlite3.Connection | None,
    source: Path,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    weight_unit: str = "lb",
    distance_unit: str = "auto",
) -> FitbitExportStats:
    """将 Fitbit 导出 ZIP 或目录写入本地 SQLite（跳过已存在日期，除非 overwrite=True）。"""
    counts: dict[str, Any] = {}
    snapshots, files_parsed = parse_fitbit_export(
        source,
        weight_unit=weight_unit,
        distance_unit=distance_unit,
        file_counts=counts,
    )
    files_failed = int(counts.get("files_failed", 0))
    failed_files = list(counts.get("failed_files") or [])
    if overwrite and not dry_run and files_failed > 0:
        sample = ", ".join(failed_files[:5])
        if len(failed_files) > 5:
            sample = f"{sample} (+{len(failed_files) - 5})"
        raise ValueError(
            t("server.importers.fitbit_zip.corrupt_overwrite_blocked", count=files_failed, files=sample)
        )
    if conn is not None:
        has_days = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'days'"
        ).fetchone() is not None
        existing = set(storage.available_dates(conn)) if has_days else set()
    else:
        existing = set()
    imported = 0
    replaced = 0
    skipped_existing = 0
    for day, raw in snapshots:
        existed = day.date in existing
        if not overwrite and existed:
            skipped_existing += 1
            continue
        imported += 1
        if existed and overwrite:
            replaced += 1
        if not dry_run:
            if conn is None:
                raise ValueError("Database connection is required when dry_run=False")
            storage.store_day(conn, day, raw, replace=overwrite)
    return {
        "files_recognized": int(counts.get("files_recognized", files_parsed)),
        "files_parsed": files_parsed,
        "files_failed": files_failed,
        "failed_files": failed_files,
        "days_found": len(snapshots),
        "imported": imported,
        "replaced": replaced,
        "skipped_existing": skipped_existing,
    }
