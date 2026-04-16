from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
from sqlalchemy import select, update
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


async def fetch_and_store_feed(session: AsyncSession, source: NewsSource, limit: int = 30) -> tuple[int, int]:
    """拉取 RSS 并入库。返回 (新增条数, 已存在但刷新 last_seen_at 的条数)。"""
    timeout = max(float(settings.rss_fetch_timeout_seconds or 10.0), 1.0)
    retries = max(int(settings.rss_fetch_retry_count or 0), 0)
    client_kwargs: dict[str, object] = {
        "timeout": httpx.Timeout(timeout=timeout, connect=min(timeout, 5.0)),
        "follow_redirects": True,
        # 避免被系统 HTTP(S)_PROXY 污染导致统一 DNS 失败；
        # 仅在显式配置 RSS_PROXY_URL 时启用代理。
        "trust_env": False,
    }
    proxy = (settings.rss_proxy_url or "").strip()
    if proxy:
        client_kwargs["proxy"] = proxy
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0 OverseasNewsFactory/1.0",
        "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
    }
    last_error: Exception | None = None
    raw = ""
    async with httpx.AsyncClient(**client_kwargs) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.get(source.rss_url, headers=headers)
                if r.status_code == 429 and attempt < retries:
                    await asyncio.sleep(1.5)
                    continue
                r.raise_for_status()
                raw = r.text
                break
            except httpx.HTTPError as e:
                last_error = e
                if attempt < retries:
                    await asyncio.sleep(0.8)
                    continue
                msg = str(e) or repr(e)
                if "Temporary failure in name resolution" in msg:
                    raise RuntimeError(
                        "域名解析失败，请检查网络/DNS，或在 backend/.env 设置 RSS_PROXY_URL（如 http://127.0.0.1:7890）"
                    ) from e
                if isinstance(e, httpx.ConnectTimeout | httpx.ReadTimeout):
                    raise RuntimeError(
                        f"抓取超时（{timeout:.1f}s），可在 backend/.env 调大 RSS_FETCH_TIMEOUT_SECONDS"
                    ) from e
                raise
    if not raw and last_error:
        raise last_error

    parsed = feedparser.parse(raw)
    added = 0
    refreshed = 0
    now = datetime.now(timezone.utc)
    for entry in parsed.entries[:limit]:
        link = entry.get("link") or entry.get("id")
        if not link:
            continue
        url = str(link).strip()
        uh = _url_hash(url)
        exists = await session.execute(select(NewsItem.id).where(NewsItem.url_hash == uh))
        if exists.scalar_one_or_none():
            await session.execute(update(NewsItem).where(NewsItem.url_hash == uh).values(last_seen_at=now))
            refreshed += 1
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
            last_seen_at=now,
        )
        session.add(item)
        added += 1

    await session.flush()
    return added, refreshed
