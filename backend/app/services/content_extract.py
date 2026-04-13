"""正文抽取与首图（MVP）；复杂站点点可扩展 Playwright 截图（完整方案）。"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
import trafilatura
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsItem, NewsSource
from app.services.candidate_score import score_news_item


async def extract_article(session: AsyncSession, item: NewsItem) -> None:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(
            item.url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NewsFactory/0.1)"},
        )
        r.raise_for_status()
        html = r.text
        final_url = str(r.url)

    extracted = trafilatura.extract(html, url=final_url, include_comments=False, include_tables=False)
    if extracted:
        item.cleaned_content = extracted.strip()[:20000]
    else:
        item.content_raw = html[:50000]

    item.hero_image_url = _guess_hero_image(html, final_url) or item.hero_image_url

    src = await session.get(NewsSource, item.source_id)
    src_name = src.name if src else ""
    tier, rules = score_news_item(item, source_name=src_name)
    item.candidate_tier = tier
    item.score_json = rules
    await session.flush()


_IMG_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_FIRST_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


def _guess_hero_image(html: str, base_url: str) -> str | None:
    m = _IMG_RE.search(html)
    if m:
        return urljoin(base_url, m.group(1).strip())
    # 兜底：尝试首个正文图片
    m2 = _FIRST_IMG_RE.search(html)
    if m2:
        candidate = urljoin(base_url, m2.group(1).strip())
        if candidate.lower().startswith(("http://", "https://")):
            return candidate
    return None
