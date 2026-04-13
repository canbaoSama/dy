from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import NewsItem, NewsSource
from app.services.us_news_sources_catalog import resolve_news_sources


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _parse_published(entry: dict[str, Any]) -> datetime | None:
    if entry.get("published_parsed"):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    if entry.get("published"):
        try:
            return parsedate_to_datetime(entry["published"])
        except Exception:
            pass
    return None


async def ensure_default_sources(session: AsyncSession) -> list[NewsSource]:
    """按配置同步信源：启用 NEWS_SOURCE_SLUGS 对应条目，其余标记为 enabled=False。"""
    resolved = resolve_news_sources(settings.news_source_slugs or None)
    want_urls = {r["rss_url"] for r in resolved}

    result = await session.execute(select(NewsSource))
    all_rows = list(result.scalars())
    by_url = {s.rss_url: s for s in all_rows}

    for s in all_rows:
        s.enabled = s.rss_url in want_urls

    for row in resolved:
        url = row["rss_url"]
        if url not in by_url:
            s = NewsSource(name=row["name"], rss_url=url, enabled=True)
            session.add(s)
            await session.flush()
            by_url[url] = s
        else:
            s = by_url[url]
            s.enabled = True
            s.name = row["name"]

    await session.flush()
    return [by_url[r["rss_url"]] for r in resolved]


async def fetch_and_store_feed(session: AsyncSession, source: NewsSource, limit: int = 15) -> int:
    """拉取 RSS 并入库；返回新增条数。"""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(source.rss_url, headers={"User-Agent": "OverseasNewsFactory/0.1"})
        r.raise_for_status()
        raw = r.text

    parsed = feedparser.parse(raw)
    added = 0
    for entry in parsed.entries[:limit]:
        link = entry.get("link") or entry.get("id")
        if not link:
            continue
        url = str(link).strip()
        uh = _url_hash(url)
        exists = await session.execute(select(NewsItem.id).where(NewsItem.url_hash == uh))
        if exists.scalar_one_or_none():
            continue

        title = (entry.get("title") or "Untitled").strip()
        title = re.sub(r"<[^>]+>", "", title)
        summary = entry.get("summary", "") or ""
        summary = re.sub(r"<[^>]+>", "", summary)[:500]
        published = _parse_published(entry)

        item = NewsItem(
            source_id=source.id,
            title=title,
            url=url,
            published_at=published,
            summary_one_liner=summary[:200] if summary else None,
            url_hash=uh,
        )
        session.add(item)
        added += 1

    await session.flush()
    return added
