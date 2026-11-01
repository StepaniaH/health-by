"""通用日期/时间工具。

约定：所有"日"都指设备本地的民用日期（civil date），不做隐式时区换算——
与 Google Health API v4 的 civil 语义保持一致。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def iso_today() -> str:
    return date.today().isoformat()


def parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def shift_iso(value: str, days: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def iso_range(end: str, days: int) -> list[str]:
    """返回 [end-days+1, ..., end]（含两端）的日期列表。"""
    end_d = date.fromisoformat(end)
    return [(end_d - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: str) -> datetime:
    """解析 ISO 时间串为 aware UTC datetime（无时区按 UTC 处理）。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
