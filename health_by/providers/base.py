"""数据源适配器契约。"""

from __future__ import annotations

from health_by.i18n import t

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """数据源通用错误（可带 HTTP 状态码）。"""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class ProviderAuthError(ProviderError):
    """授权失效：需要用户重新连接数据源。"""

    def __init__(self, message: str, *, needs_reconnect: bool = True):
        super().__init__(message, status=401)
        self.needs_reconnect = needs_reconnect


@dataclass
class RawDay:
    """某一天同步得到的原始载荷：按天留档、可重算。"""

    date: str
    source: str
    generated_at: str
    payload: dict[str, Any] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    # 解析层把「有值但读不出来」的字段记在这里，和 errors（请求失败）区分开：
    # 请求成功但字段改名/类型变化时 errors 是空的，这类静默失败只有这里能看见。
    parse_issues: list[dict] = field(default_factory=list)


class Provider(ABC):
    """数据源契约。

    必须实现：fetch_raw / translate。
    OAuth 类数据源（auth_kind == "oauth"）还需实现：auth_status / build_auth_url /
    exchange_code / refresh / revoke。
    """

    name: str = ""
    display_name: str = ""
    auth_kind: str = "none"  # none | oauth

    # ---- 数据 ----

    def fetch_raw(self, date: str, *, credentials: dict | None = None, http=None, on_progress=None) -> RawDay:
        raise NotImplementedError

    def translate(self, raw: RawDay):
        raise NotImplementedError

    # ---- OAuth（auth_kind == "oauth" 时实现）----

    def new_pkce(self) -> tuple[str, str]:
        raise ProviderError(t('server.providers.base.new_pkce.ac3e082b1'))

    def auth_status(self, credentials: dict | None) -> dict:
        return {"configured": False, "connected": False, "needs_reconnect": False}

    def build_auth_url(self, *, credentials: dict, redirect_uri: str, state: str, code_challenge: str) -> str:
        raise ProviderError(t('server.providers.base.build_auth_url.ac3e082b1'))

    def exchange_code(self, *, credentials: dict, redirect_uri: str, code: str, code_verifier: str, http) -> dict:
        raise ProviderError(t('server.providers.base.exchange_code.ac3e082b1'))

    def refresh(self, *, credentials: dict, http) -> dict:
        raise ProviderError(t('server.providers.base.refresh.ac3e082b1'))

    def revoke(self, *, credentials: dict, http) -> None:
        return None
