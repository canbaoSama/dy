from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobStatus, VideoJob

STUCK_STATUSES: tuple[str, ...] = (
    JobStatus.extracting_content.value,
    JobStatus.scoring_candidate.value,
    JobStatus.generating_script.value,
    JobStatus.collecting_assets.value,
    JobStatus.generating_audio.value,
    JobStatus.building_timeline.value,
    JobStatus.rendering_video.value,
)


async def recover_stuck_jobs(
    session: AsyncSession,
    *,
    stale_minutes: int = 8,
    reason_prefix: str = "任务长时间卡住，已自动恢复为失败，可直接重试",
) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, stale_minutes))
    result = await session.execute(
        select(VideoJob).where(
            VideoJob.status.in_(STUCK_STATUSES),
            VideoJob.updated_at.is_not(None),
            VideoJob.updated_at < cutoff,
        )
    )
    rows = list(result.scalars().all())
    repaired = 0
    for job in rows:
        job.status = JobStatus.failed.value
        if not job.failed_stage:
            job.failed_stage = "stuck_recovery"
        tip = f"{reason_prefix}（阈值 {stale_minutes} 分钟）"
        old = (job.error_message or "").strip()
        job.error_message = f"{tip}\n{old}"[:2000] if old else tip
        repaired += 1
    if repaired:
        await session.commit()
    return {"scanned": len(rows), "repaired": repaired}
