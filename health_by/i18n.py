"""Shared UI messages. Model instructions are deliberately outside this module.

Locale selection is request-local: concurrent users cannot change each other's
language. Catalog entries own sentence order, units and plural forms; numerical
formatting helpers must not assemble language-specific fragments.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
from string import Formatter

DEFAULT_LANGUAGE = "zh"
_language: ContextVar[str] = ContextVar("health_by_language", default=DEFAULT_LANGUAGE)
_group_numbers: ContextVar[bool] = ContextVar("health_by_group_numbers", default=True)
ROOT = Path(__file__).parent / "locales"
PLURAL_PARAMETERS = json.loads((ROOT / "plurals.json").read_text())
CATALOGS = {lang: json.loads((ROOT / f"{lang}.json").read_text()) for lang in ("zh", "en")}


def language(value: str | None = None) -> str:
    if value is None:
        return _language.get()
    for candidate in value.split(","):
        code = candidate.split(";", 1)[0].strip().lower().split("-", 1)[0]
        if code in CATALOGS:
            return code
    return DEFAULT_LANGUAGE


@contextmanager
def using_language(value: str | None, *, group_numbers: bool = True):
    token = _language.set(language(value))
    number_token = _group_numbers.set(group_numbers)
    try:
        yield
    finally:
        _language.reset(token)
        _group_numbers.reset(number_token)


class Message(str):
    """A string-compatible key and arguments, including module-level labels.

    Rendering at the API boundary keeps imported labels in the request's locale.
    Stored/model-generated/user-authored strings are never reverse-translated.
    """
    def __new__(cls, key: str, params: dict):
        obj = super().__new__(cls, _render(key, params))
        obj.key, obj.params = key, params
        return obj

    def __reduce__(self):
        return Message, (self.key, self.params)

    def __str__(self):
        return _render(self.key, self.params)

    def __format__(self, spec):
        return format(str(self), spec)

    def __add__(self, other):
        return str(self) + str(other)

    def __radd__(self, other):
        return str(other) + str(self)

    def format(self, *args, **kwargs):
        if args:
            return str(self).format(*args, **kwargs)
        return Message(self.key, {**self.params, **kwargs})

    def strip(self, *args):
        return str(self).strip(*args)

    def expandtabs(self, *args):
        return str(self).expandtabs(*args)

    def join(self, items):
        return str(self).join(str(item) for item in items)


class _CatalogFormatter(Formatter):
    def format_field(self, value, spec):
        if _group_numbers.get() and isinstance(value, (int, float)) and not isinstance(value, bool):
            if not spec:
                return format(value, ",")
            if spec.startswith(".") and spec.endswith(("f", "g")):
                spec = "," + spec
        return super().format_field(value, spec)


_FORMATTER = _CatalogFormatter()


def _render(key, params):
    catalog = CATALOGS[_language.get()]
    if "count" in params:
        plural = "one" if params["count"] == 1 else "other"
        if f"{key}.{plural}" in catalog:
            key = f"{key}.{plural}"
    if key in PLURAL_PARAMETERS:
        def form(name):
            try:
                count = float(str(params.get(name)).replace(",", ""))
            except (TypeError, ValueError):
                return "other"
            return "one" if count == 1 else "other"
        key = ".".join([key, *(form(name) for name in PLURAL_PARAMETERS[key])])
    template = catalog.get(key)
    if template is None:
        raise KeyError(f"Unknown translation key: {key}")
    if not params:
        return template
    return _FORMATTER.vformat(template, (), {name: str(value) if isinstance(value, Message) else value for name, value in params.items()})


def t(key: str, **params) -> Message:
    return Message(key, params)


def localize(value):
    if isinstance(value, Message):
        return str(value)
    if isinstance(value, dict):
        return {key: localize(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [localize(child) for child in value]
    return value


def number(value, digits=0):
    return f"{value:,.{digits}f}"


