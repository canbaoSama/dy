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
_VIDEO_META_RE = re.compile(
    r'<meta[^>]+property=["\'](?:og:video|og:video:url|twitter:player:stream)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_VIDEO_TAG_RE = re.compile(r'<video[^>]+src=["\']([^"\']+)["\']', re.I)
_VIDEO_SOURCE_RE = re.compile(r'<source[^>]+src=["\']([^"\']+)["\']', re.I)
_VIDEO_URL_RE = re.compile(r'https?:\/\/[^\s"\'<>]+(?:\.m3u8|\.mp4|\.webm)[^\s"\'<>]*', re.I)


def _looks_like_noise_asset(url: str) -> bool:
    low = (url or "").lower()
    bad_words = (
        "share_icon",
        "icon/",
        "/icons/",
        "logo",
        "qrcode",
        "wechat",
        "weibo",
        "facebook",
        "twitter",
        "attention.jpg",
        "about-news",
        "gwab",
    )
    return any(w in low for w in bad_words)


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


def extract_media_candidates_from_html(
    html: str,
    base_url: str,
    *,
    max_images: int = 16,
    max_videos: int = 8,
) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def push(url: str, asset_type: str, source: str) -> None:
        u = urljoin(base_url, (url or "").strip())
        if not u.startswith(("http://", "https://")):
            return
        if _looks_like_noise_asset(u):
            return
        if u in seen:
            return
        seen.add(u)
        out.append({"url": u, "asset_type": asset_type, "source": source})

    for m in _IMG_RE.finditer(html):
        push(m.group(1), "user_image", "og:image")
        if sum(1 for x in out if x["asset_type"] == "user_image") >= max_images:
            break
    if sum(1 for x in out if x["asset_type"] == "user_image") < max_images:
        for m in _FIRST_IMG_RE.finditer(html):
            push(m.group(1), "user_image", "img")
            if sum(1 for x in out if x["asset_type"] == "user_image") >= max_images:
                break

    for pattern in (_VIDEO_META_RE, _VIDEO_TAG_RE, _VIDEO_SOURCE_RE):
        for m in pattern.finditer(html):
            push(m.group(1), "user_video", "video")
            if sum(1 for x in out if x["asset_type"] == "user_video") >= max_videos:
                break
        if sum(1 for x in out if x["asset_type"] == "user_video") >= max_videos:
            break
    if sum(1 for x in out if x["asset_type"] == "user_video") < max_videos:
        for m in _VIDEO_URL_RE.finditer(html):
            push(m.group(0), "user_video", "video_url")
            if sum(1 for x in out if x["asset_type"] == "user_video") >= max_videos:
                break

    return out


async def fetch_media_candidates(article_url: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        r = await client.get(
            article_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsFactory/0.1)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        r.raise_for_status()
    return extract_media_candidates_from_html(r.text, str(r.url))
