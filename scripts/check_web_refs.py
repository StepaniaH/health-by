#!/usr/bin/env python3
"""静态检查：JS 里引用的 DOM id 是否都真实存在于它所在的页面模板里。

防的是什么
    pytest 全绿 ≠ 页面能渲染。web/ 下的 JS 没有任何测试覆盖，`$("x").textContent = ...`
    在 x 不存在时会抛 TypeError 并**中断整条渲染链**——2026-10-30 就因此让 04/05/06
    三个区块连同页面元信息一起消失，而 183 个 Python 测试全是绿的。

检查规则
    - 按页配对：index.html ↔ dashboard.js、settings.html ↔ settings.js；api.js 被两页共用，
      对两页取并集。
    - 覆盖三种取元素写法：`$("id")`、`getElementById("id")`、`querySelector("#id")`。
    - 只查**字符串字面量**里的 id，动态拼接（`$(`card-${x}`)`）跳过并计数，避免误报。
    - 模板里出现的 id 以 HTML 为准；JS 里 `document.createElement` 动态生成的元素不参与。

用法
    python scripts/check_web_refs.py          # 无输出=通过
    python scripts/check_web_refs.py -v       # 打印统计与跳过项
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "health_by" / "web"

PAGES = {
    "index.html": ["js/api.js", "js/format.js", "js/charts.js", "js/dashboard.js", "js/ai.js"],
    "settings.html": ["js/api.js", "js/format.js", "js/settings.js"],
}

ID_ATTR = re.compile(r'\bid="([^"]+)"')
DOLLAR = re.compile(r'\$\("([^"]+)"\)')
GET_BY_ID = re.compile(r'getElementById\("([^"]+)"\)')
QUERY_HASH = re.compile(r'querySelector(?:All)?\("#([^"]+)"\)')
DYNAMIC = re.compile(r'\$\(`|getElementById\(`|querySelector\(`')


def ids_in_html(path: Path) -> set[str]:
    return set(ID_ATTR.findall(path.read_text(encoding="utf-8")))


def ids_in_js(path: Path) -> tuple[dict[str, int], int]:
    """返回 {id: 首次出现的行号} 与动态拼接的处数。"""
    found: dict[str, int] = {}
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in (DOLLAR, GET_BY_ID, QUERY_HASH):
            for dom_id in pattern.findall(line):
                found.setdefault(dom_id, lineno)
    return found, len(DYNAMIC.findall(text))


def collect_problems(web: Path = WEB) -> tuple[list[str], dict[str, int]]:
    """返回 (问题列表, 每页跳过的动态拼接计数)。问题为空即通过。"""
    problems: list[str] = []
    dynamic_skipped: dict[str, int] = {}
    for page, scripts in PAGES.items():
        page_path = web / page
        if not page_path.exists():
            problems.append(f"{page}: 模板不存在")
            continue
        declared = ids_in_html(page_path)
        referenced: dict[str, tuple[str, int]] = {}
        dynamic = 0
        for script in scripts:
            script_path = web / script
            if not script_path.exists():
                continue
            ids, dyn = ids_in_js(script_path)
            dynamic += dyn
            for dom_id, lineno in ids.items():
                referenced.setdefault(dom_id, (script, lineno))
        dynamic_skipped[page] = dynamic
        for dom_id, (script, lineno) in sorted(referenced.items()):
            if dom_id not in declared:
                problems.append(
                    f"{web.name}/{script}:{lineno} 引用了 #{dom_id}，"
                    f"但它不在 {page} 里"
                )
    return problems, dynamic_skipped


def unused_ids(web: Path = WEB) -> list[str]:
    """空元素（开标签后面紧接着闭标签）且没有任何脚本引用它 → 死元素。

    与 collect_problems() 方向相反：那边查"JS 引用了不存在的 id"，这边查
    "HTML 留了个空壳但没人填"。死元素让人分不清「有意留空」和「漏了赋值」，
    而两者只差一次删除。有静态文本的元素不算——它们本来就不需要脚本。

    注意：不能用"配对标签"的办法找内容，因为 id 元素常嵌在
    section/div 里，外层配对会把内层吞掉（今天踩过）。这里只看开标签之后
    紧接的是不是闭标签。
    """
    dead: list[str] = []
    for page, scripts in PAGES.items():
        page_path = web / page
        if not page_path.exists():
            continue
        html = page_path.read_text(encoding="utf-8")
        scripts_text = ""
        for script in scripts:
            script_path = web / script
            if script_path.exists():
                scripts_text += script_path.read_text(encoding="utf-8")
        for match in re.finditer(r'id="([^"]+)"', html):
            dom_id = match.group(1)
            tag_end = html.find(">", match.end())
            if tag_end < 0:
                continue
            following = html[tag_end + 1:].lstrip()
            if not following.startswith("</"):
                continue  # 有内容，不是空壳
            if f'"{dom_id}"' in scripts_text or f"'{dom_id}'" in scripts_text:
                continue  # 脚本会填它
            dead.append(f"{page}: #{dom_id} 是空元素且没有任何脚本引用")
    return dead


def main() -> int:
    parser = argparse.ArgumentParser(description="JS 引用的 DOM id 必须存在于对应页面")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    problems, dynamic_skipped = collect_problems()
    problems += unused_ids()
    total_dynamic = sum(dynamic_skipped.values())
    if args.verbose:
        for page, count in dynamic_skipped.items():
            print(f"{page}: 跳过动态拼接 {count} 处")
    if total_dynamic:
        # 计数不是装饰：跳过意味着"这些引用没被检查"，所以要让它看起来像件事，而不是一个永远为 0 的计数器
        for page, count in dynamic_skipped.items():
            if count:
                print(
                    f"注意：{page} 有 {count} 处动态拼接的 id 未被本检查覆盖（盲区），"
                    "改动这些地方的引用时需要人工确认。",
                    file=sys.stderr,
                )

    if problems:
        print("web/ 引用检查未通过：", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\n这类错误只会让渲染在中途抛错——出错的区块之后的区块和页头元信息会一起消失，"
            "而 Python 测试看不到。修法：要么把元素加回模板，要么删掉这行引用。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
