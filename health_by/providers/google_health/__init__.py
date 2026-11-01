"""Google Health API v4 数据源。

- translate：原始响应 → DaySnapshot（纯函数，测试充分）
- client / provider：OAuth 与同步实现（provider.py 注册进数据源注册表）
"""

from .translate import translate_payload

__all__ = ["translate_payload"]
