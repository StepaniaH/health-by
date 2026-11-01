"""输入协议独立于写作提示词；窗口协议不随单日协议升级。"""

DAILY_SCHEMA_VERSION = "daily-v2"
WINDOW_SCHEMA_VERSION = "window-v1"


def version_for_window(start: str, end: str) -> str:
    return DAILY_SCHEMA_VERSION if start == end else WINDOW_SCHEMA_VERSION

MODEL_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
