"""数据源适配器注册表。

内置实现按需加载（ensure_builtin_providers），避免导入环。
"""

from __future__ import annotations

from health_by.i18n import t

from .base import Provider, ProviderAuthError, ProviderError, RawDay

PROVIDERS: dict[str, Provider] = {}
_builtins_loaded = False


def register(provider: Provider) -> Provider:
    PROVIDERS[provider.name] = provider
    return provider


def available_providers() -> list[str]:
    ensure_builtin_providers()
    return sorted(PROVIDERS)


def get_provider(name: str) -> Provider:
    ensure_builtin_providers()
    if name not in PROVIDERS:
        raise LookupError(t('server.providers.__init__.get_provider.35f878c86', name=name, p2=', '.join(sorted(PROVIDERS))))
    return PROVIDERS[name]


def ensure_builtin_providers() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    from .google_health.provider import GoogleHealthProvider

    register(GoogleHealthProvider())
    _builtins_loaded = True
