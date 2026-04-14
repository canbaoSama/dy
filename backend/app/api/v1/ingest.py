from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.rss_ingest import ensure_default_sources, fetch_and_store_feed

router = APIRouter(tags=["ingest"])


@router.post("/ingest/trigger")
async def ingest_trigger(session: AsyncSession = Depends(get_db)):
    """手动触发抓取（MVP Phase 2）；完整方案由 worker-news 定时/队列触发同一逻辑。"""
    sources = await ensure_default_sources(session)
    total = 0
    refreshed = 0
    failed: list[dict[str, str]] = []
    for s in sources:
        if s.enabled:
            try:
                a, r = await fetch_and_store_feed(session, s)
                total += a
                refreshed += r
            except Exception as e:  # noqa: BLE001
                failed.append(
                    {
                        "source": s.name,
                        "rss_url": s.rss_url,
                        "error": (str(e) or repr(e))[:300],
                    }
                )
    await session.commit()
    rows = await query_candidate_rows(session, limit=15)
    items = await serialize_candidates(rows)
    hot_top = [
        {
            "index": x.index,
            "source": x.source,
            "title": x.title_zh or x.title,
            "score_10": x.score_10,
            "heat_index": x.heat_index,
            "tier": x.tier,
        }
        for x in items[:5]
    ]
    return {
        "added": total,
        "refreshed": refreshed,
        "sources": len(sources),
        "failed_count": len(failed),
        "failed": failed,
        "hot_top": hot_top,
        "candidates": [x.model_dump() for x in items[:15]],
    }
