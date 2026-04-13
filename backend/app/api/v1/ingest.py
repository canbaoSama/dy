from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.rss_ingest import ensure_default_sources, fetch_and_store_feed

router = APIRouter(tags=["ingest"])


@router.post("/ingest/trigger")
async def ingest_trigger(session: AsyncSession = Depends(get_db)):
    """手动触发抓取（MVP Phase 2）；完整方案由 worker-news 定时/队列触发同一逻辑。"""
    sources = await ensure_default_sources(session)
    total = 0
    failed: list[dict[str, str]] = []
    for s in sources:
        if s.enabled:
            try:
                total += await fetch_and_store_feed(session, s)
            except Exception as e:  # noqa: BLE001
                failed.append(
                    {
                        "source": s.name,
                        "rss_url": s.rss_url,
                        "error": str(e)[:300],
                    }
                )
    await session.commit()
    return {
        "added": total,
        "sources": len(sources),
        "failed_count": len(failed),
        "failed": failed,
    }
