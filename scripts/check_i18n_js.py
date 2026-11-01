"""静态检查 web 前端是否还留有未走词典的中文、语言绑定格式。

只做三件事，都对应一个真实错误：

1. 字符串字面量里出现汉字 —— 说明有文案没进词典。
2. 调用 toLocaleString / toLocaleDateString / toLocaleTimeString —— 语言被写死在代码里。
注释里的汉字是允许的：那是写给人看的，不显示给用户。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]")
LOCALE_LITERAL = re.compile(r"^(?:zh-CN|en-US)$", re.IGNORECASE)
LOCALE_CALLS = ("toLocaleString", "toLocaleDateString", "toLocaleTimeString")

EXEMPT = {
    Path("health_by/web/js/i18n.js"),          # 语言选择与 t() 本身
    Path("health_by/web/js/i18n-messages.js"),  # 词典正文
}

# 这些字符之后出现的 `/` 是正则字面量的开头，不是除号。
REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^")


def scan(source: str) -> list[tuple[int, str]]:
    """返回 (行号, 问题)。注释被跳过，字符串按字面量检查。"""
    problems: list[tuple[int, str]] = []
    line = 1
    index = 0
    length = len(source)
    previous = ""

    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""

        if char == "\n":
            line += 1
            index += 1
            continue

        if char == "/" and nxt == "/":
            index = source.find("\n", index)
            if index == -1:
                break
            continue

        if char == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            end = length if end == -1 else end + 2
            line += source.count("\n", index, end)
            index = end
            continue

        if char == "/" and (previous == "" or previous in REGEX_PRECEDERS):
            index += 1
            escaped = False
            while index < length:
                current = source[index]
                if current == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == "/":
                    index += 1
                    break
                index += 1
            previous = "/"
            continue

        if char in "\"'`":
            quote = char
            start_line = line
            index += 1
            text: list[str] = []
            depth = 0
            while index < length:
                current = source[index]
                if current == "\\":
                    index += 2
                    continue
                if quote == "`" and current == "$" and index + 1 < length and source[index + 1] == "{":
                    depth += 1
                    index += 2
                    continue
                if quote == "`" and current == "}" and depth:
                    depth -= 1
                    index += 1
                    continue
                if current == quote and depth == 0:
                    index += 1
                    break
                if current == "\n":
                    line += 1
                if not (quote == "`" and depth):
                    text.append(current)
                index += 1
            value = "".join(text)
            if HAN.search(value):
                problems.append((start_line, "unlocalized string literal"))
            if LOCALE_LITERAL.match(value):
                problems.append((start_line, "locale outside i18n module"))
            previous = quote
            continue

        if char.isalpha() or char == "_":
            start = index
            while index < length and (source[index].isalnum() or source[index] in "_$"):
                index += 1
            word = source[start:index]
            if word in LOCALE_CALLS:
                problems.append((line, "locale formatting outside i18n module"))
            previous = word[-1]
            continue

        if not char.isspace():
            previous = char
        index += 1

    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("health_by/web/js")
    failed = False
    for path in sorted(root.glob("*.js")):
        if path in EXEMPT:
            continue
        for line, problem in scan(path.read_text(encoding="utf-8")):
            print(f"{path}:{line}: {problem}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
