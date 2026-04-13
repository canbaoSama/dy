"""候选新闻列表：供 HTTP 与聊天命令复用（运营层与 API 共用数据）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import NewsItem
from app.schemas import CandidateItem
from app.services.candidate_translate import translate_to_zh


async def query_candidate_rows(session: AsyncSession, limit: int = 50) -> list[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    q = (
        select(NewsItem)
        .options(selectinload(NewsItem.source))
        .where((NewsItem.published_at.is_(None)) | (NewsItem.published_at >= cutoff))
        .order_by(nulls_last(NewsItem.published_at.desc()), NewsItem.id.desc())
        .limit(limit)
    )
    result = await session.execute(q)
    return list(result.scalars().all())


async def serialize_candidates(rows: list[NewsItem]) -> list[CandidateItem]:
    async def _safe_translate(text: str) -> str:
        try:
            return await asyncio.wait_for(translate_to_zh(text), timeout=1.8)
        except Exception:
            return text

    titles = [it.title or "" for it in rows]
    zh_titles = await asyncio.gather(*[_safe_translate(t) for t in titles])

    out: list[CandidateItem] = []
    for i, it in enumerate(rows, start=1):
        src = it.source.name if it.source else ""
        title_zh = zh_titles[i - 1]
        raw_total = 0.0
        if isinstance(it.score_json, dict):
            try:
                raw_total = float(it.score_json.get("total") or 0.0)
            except Exception:
                raw_total = 0.0
        score_10 = round(max(0.0, min(raw_total * 10.0 / 7.0, 10.0)), 1) if raw_total else None
        out.append(
            CandidateItem(
                index=i,
                title=it.title,
                title_zh=title_zh,
                source=src,
                score_10=score_10,
                published_at=it.published_at,
                summary=it.summary_one_liner,
                summary_zh=it.summary_one_liner,
                tier=it.candidate_tier,
                url=it.url,
            )
        )
    return out
