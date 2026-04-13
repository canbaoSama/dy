"""网页首屏截图（完整方案：Playwright）。MVP 不强制安装浏览器。"""

from __future__ import annotations

from pathlib import Path


async def screenshot_homepage(url: str, dest: Path) -> str | None:
    """
    返回本地截图路径；未实现时返回 None，由候选评分标记 has_screenshot=false。
    接入示例：async with async_playwright() as p: ...
    """
    return None
