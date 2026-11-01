"""JS 引用的 DOM id 必须在它所在的页面模板里存在。

为什么这条比 UI 测试便宜且更早：
    `web/` 下的脚本没有任何测试覆盖，而一个 `$("id").textContent = ...` 指向已被
    删除的元素时，会在渲染中途抛 TypeError，**它之后的区块连同页头元信息一起消失**。
    2026-10-30 就因此让 04/05/06 三块整块不显示，而 183 个 Python 测试全绿、API 正常。
    这类错误是静态可判定的：读一遍模板和脚本即可，不需要起服务、不需要浏览器，
    毫秒级，且能在提交前就拦住。渲染后的"六块标题齐全"仍要在验收时用浏览器确认——
    两层检查抓的不是同一件事。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_web_refs import collect_problems  # noqa: E402


def test_web_scripts_only_reference_ids_that_exist() -> None:
    problems, dynamic_skipped = collect_problems()
    for page, count in dynamic_skipped.items():
        # 盲区要看得见：动态拼接的 id 这条检查覆盖不到。
        # 非 0 时也要显眼——它意味着有引用没被检查，而不是一个永远为 0 的计数器。
        level = "注意" if count else "统计"
        print(f"[web refs] {level}：{page} 跳过动态拼接 {count} 处（检查盲区）")
    assert not problems, "JS 引用了模板里不存在的 id：\n" + "\n".join(problems)


def test_the_checker_is_usable_on_its_own() -> None:
    """脚本的 CLI 契约：无输出 = 通过、exit 0；发现问题时 exit 1 且点名文件:行。

    导入 `collect_problems()` 只证明检查逻辑对；**脚本还要能被单独跑**
    （提交前自检、交付前复查）。这两件事会各自坏掉，所以分开断言。
    """
    import subprocess

    script = ROOT / "scripts" / "check_web_refs.py"
    assert script.exists(), f"静态检查脚本缺失：{script}"

    ok = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0, f"检查未通过：\n{ok.stdout}{ok.stderr}"
    assert ok.stdout.strip() == "", f"通过时应无输出，实际：{ok.stdout!r}"
