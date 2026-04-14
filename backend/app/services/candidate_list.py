"""候选新闻列表：供 HTTP 与聊天命令复用（运营层与 API 共用数据）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import NewsItem, NewsSource
from app.schemas import CandidateItem
from app.services.candidate_translate import translate_to_zh
from app.services.us_news_sources_catalog import SOURCE_CATALOG, resolve_news_sources


def _us_eastern_midnight_utc() -> datetime:
    """美国东部日历日 00:00 对应的 UTC 时刻（含夏令时）。"""
    et = ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    return datetime.combine(now_et.date(), dt_time.min, tzinfo=et).astimezone(timezone.utc)


def _source_heat_boost(source_name: str) -> float:
    s = (source_name or "").lower()
    if "google trends" in s:
        return 1.8
    if "google news" in s:
        return 1.6
    if "reddit" in s:
        return 1.4
    if "reuters" in s:
        return 1.25
    if "bbc" in s:
        return 1.2
    return 1.0


def _source_traffic_weight(source_name: str) -> float:
    """
    近似“平台流量/覆盖面”权重（1.0 为基准）：
    用于让高流量平台在最终候选排序里占更高比例。
    """
    s = (source_name or "").lower()
    if "google news" in s:
        return 1.35
    if "reuters" in s:
        return 1.22
    if "bbc" in s:
        return 1.18
    if "google trends" in s:
        return 1.15
    if "reddit" in s:
        return 1.10
    if "ap " in s or "ap news" in s:
        return 1.12
    return 1.0


def _fallback_heat_score(item: NewsItem) -> float:
    """当尚未完成候选打分时，用来源权重+时效性给一个 0~10 热度分。"""
    now = datetime.now(timezone.utc)
    rank_at = item.last_seen_at or item.published_at or item.created_at or now
    if rank_at.tzinfo is None:
        rank_at = rank_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - rank_at).total_seconds() / 3600.0)
    # 0 小时=1.0，24 小时约 0.4，48 小时约 0.1
    freshness = max(0.1, 1.0 - min(age_hours, 48.0) / 40.0)
    src = item.source.name if item.source else ""
    raw = 6.0 * freshness * _source_heat_boost(src)
    return round(max(0.0, min(raw, 10.0)), 1)


def _item_heat_score_10(item: NewsItem) -> float:
    raw_total = 0.0
    if isinstance(item.score_json, dict):
        try:
            raw_total = float(item.score_json.get("total") or 0.0)
        except Exception:
            raw_total = 0.0
    if raw_total:
        return round(max(0.0, min(raw_total * 10.0 / 7.0, 10.0)), 1)
    return _fallback_heat_score(item)


def _item_recency_score_10(item: NewsItem) -> float:
    now = datetime.now(timezone.utc)
    rank_at = item.last_seen_at or item.published_at or item.created_at or now
    if rank_at.tzinfo is None:
        rank_at = rank_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - rank_at).total_seconds() / 3600.0)
    # 0 小时=10 分，24 小时≈5.2 分，48 小时≈1.0 分
    return round(max(1.0, 10.0 - min(age_hours, 48.0) * 0.2), 1)


def _item_composite_rank(item: NewsItem) -> float:
    """
    综合排序分：
    - 热度分（模型或兜底）: 55%
    - 来源流量权重折算: 30%
    - 时效分: 15%
    """
    src = item.source.name if item.source else ""
    heat10 = _item_heat_score_10(item)
    traffic10 = max(0.0, min(_source_traffic_weight(src) * 7.0, 10.0))
    recency10 = _item_recency_score_10(item)
    return round(heat10 * 0.55 + traffic10 * 0.30 + recency10 * 0.15, 3)


_SOURCE_ZH_BY_RSS: dict[str, str] = {r["rss_url"]: r["name_zh"] for r in SOURCE_CATALOG}


def _item_heat_index(item: NewsItem) -> int:
    """
    热度指数（整数）：
    结合热度分、来源权重、时效分，映射到约 1000~20000+ 区间，便于直观比较。
    """
    heat10 = _item_heat_score_10(item)
    src_w = _source_traffic_weight(item.source.name if item.source else "")
    rec10 = _item_recency_score_10(item)
    val = int(round(heat10 * 1200 + src_w * 2600 + rec10 * 350))
    return max(100, val)


async def query_candidate_rows(session: AsyncSession, limit: int = 50) -> list[NewsItem]:
    """
    优先「今日头条式」：各 RSS 仍在头条区出现的稿件会刷新 last_seen_at，按该时间 + 发布时间综合排序；
    时间窗以美东「当天 0 点」起算，条数过少则回退到最近 36 小时，避免凌晨空白。
    """
    rank_ts = func.coalesce(NewsItem.last_seen_at, NewsItem.published_at, NewsItem.created_at)
    day_start = _us_eastern_midnight_utc()
    roll_start = datetime.now(timezone.utc) - timedelta(hours=36)
    allowed_rss_urls = {x["rss_url"] for x in resolve_news_sources(settings.news_source_slugs or None)}

    async def _fetch(cutoff: datetime) -> list[NewsItem]:
        q = (
            select(NewsItem)
            .options(selectinload(NewsItem.source))
            .join(NewsSource, NewsSource.id == NewsItem.source_id)
            .where(
                rank_ts >= cutoff,
                NewsSource.enabled.is_(True),
                NewsSource.rss_url.in_(allowed_rss_urls),
            )
            .order_by(rank_ts.desc(), NewsItem.id.desc())
            .limit(max(limit * 4, 80))
        )
        r = await session.execute(q)
        return list(r.scalars().all())

    rows = await _fetch(day_start)
    if len(rows) < 8:
        rows = await _fetch(roll_start)
    if len(rows) < 3:
        rows = await _fetch(datetime.now(timezone.utc) - timedelta(days=7))
    rows.sort(key=lambda x: (_item_composite_rank(x), x.id), reverse=True)
    return rows[:limit]


async def serialize_candidates(rows: list[NewsItem]) -> list[CandidateItem]:
    async def _safe_translate(text: str) -> str:
        try:
            return await translate_to_zh(text)
        except Exception:
            return text

    titles = [it.title or "" for it in rows]
    summaries = [it.summary_one_liner or "" for it in rows]
    src_keys: list[str] = []
    src_seen: set[str] = set()
    for it in rows:
        s = it.source.name if it.source else ""
        if s and s not in src_seen:
            src_seen.add(s)
            src_keys.append(s)

    tasks: list = []
    tasks.extend(_safe_translate(t) for t in titles)
    tasks.extend(_safe_translate(s) for s in summaries)
    tasks.extend(_safe_translate(s) for s in src_keys)
    flat = await asyncio.gather(*tasks)
    n = len(rows)
    zh_titles = flat[0:n]
    zh_summaries = flat[n : n * 2]
    zh_src_vals = flat[n * 2 :]
    src_zh_map = dict(zip(src_keys, zh_src_vals)) if src_keys else {}

    out: list[CandidateItem] = []
    for i, it in enumerate(rows, start=1):
        src = it.source.name if it.source else ""
        title_zh = zh_titles[i - 1]
        summary_zh = zh_summaries[i - 1]
        source_display = (
            _SOURCE_ZH_BY_RSS.get((it.source.rss_url if it.source else "") or "")
            or src_zh_map.get(src, src)
            if src
            else ""
        )
        score_10 = _item_heat_score_10(it)
        heat_index = _item_heat_index(it)
        source_weight = round(_source_traffic_weight(src), 2)
        recency_score_10 = _item_recency_score_10(it)
        rank_score_10 = round(max(0.0, min(_item_composite_rank(it), 10.0)), 1)
        out.append(
            CandidateItem(
                index=i,
                title=it.title,
                title_zh=title_zh,
                source=source_display or src,
                score_10=score_10,
                heat_index=heat_index,
                source_weight=source_weight,
                recency_score_10=recency_score_10,
                rank_score_10=rank_score_10,
                published_at=it.published_at,
                summary=it.summary_one_liner,
                summary_zh=summary_zh or (it.summary_one_liner or ""),
                tier=it.candidate_tier,
                url=it.url,
            )
        )
    return out
