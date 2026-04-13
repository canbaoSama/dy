from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import JobAsset, JobStatus, ScriptRecord, SubtitleTimeline, VideoJob
from app.schemas import AudioOutputBrief, JobDetailOut, JobOut, VideoOutputBrief
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.pipeline import run_job_pipeline

router = APIRouter(tags=["jobs"])


@router.post("/jobs/from-candidate")
async def create_job_from_candidate(
    index: int = Query(..., ge=1),
    duration_sec: int = 35,
    style: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    rows = await query_candidate_rows(session)
    if index < 1 or index > len(rows):
        raise HTTPException(400, "候选序号超出范围，请先刷新候选列表")
    item = rows[index - 1]
    job = VideoJob(
        news_item_id=item.id,
        status=JobStatus.created.value,
        duration_sec=duration_sec,
        style_notes=style or "快讯",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return {"job": JobOut.model_validate(job)}


async def _run_pipeline_bg(job_id: int) -> None:
    async with SessionLocal() as session:
        await run_job_pipeline(session, job_id)


@router.post("/jobs/{job_id}/pipeline")
async def trigger_pipeline(job_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_pipeline_bg, job_id)
    return {"ok": True, "job_id": job_id, "note": "管线异步执行，请轮询 GET /jobs/{id} 或 GET /jobs/{id}/detail"}


@router.post("/jobs/{job_id}/assets/upload")
async def upload_job_assets(
    job_id: int,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    if not files:
        raise HTTPException(400, "未提供文件")

    save_dir = settings.assets_dir / f"job_{job_id}" / "user_uploads"
    save_dir.mkdir(parents=True, exist_ok=True)
    accepted = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v", ".webm"}
    added: list[dict] = []

    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        guessed = mimetypes.guess_extension(f.content_type or "") or ""
        if not ext and guessed:
            ext = guessed.lower()
        if ext not in accepted:
            continue
        raw = await f.read()
        if not raw:
            continue
        safe_name = f"upload_{len(added)}{ext}"
        p = save_dir / safe_name
        p.write_bytes(raw)
        asset_type = "user_video" if ext in {".mp4", ".mov", ".m4v", ".webm"} else "user_image"
        session.add(
            JobAsset(
                job_id=job_id,
                asset_type=asset_type,
                local_path=str(p),
                meta_json={"from": "chat_upload", "filename": f.filename},
            )
        )
        added.append({"name": f.filename, "asset_type": asset_type, "path": str(p)})

    if not added:
        raise HTTPException(400, "仅支持图片/视频文件（jpg/png/webp/mp4/mov/webm）")
    await session.commit()
    return {"ok": True, "job_id": job_id, "added": len(added), "files": added}


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, session: AsyncSession = Depends(get_db)):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    return {"job": JobOut.model_validate(job)}


@router.get("/jobs/{job_id}/detail", response_model=JobDetailOut)
async def get_job_detail(job_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(VideoJob)
        .options(
            selectinload(VideoJob.audios),
            selectinload(VideoJob.videos),
        )
        .where(VideoJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "job 不存在")

    r_sc = await session.execute(
        select(ScriptRecord)
        .where(ScriptRecord.job_id == job_id)
        .order_by(ScriptRecord.version.desc())
        .limit(1)
    )
    sc = r_sc.scalar_one_or_none()
    latest_script = json.loads(sc.payload_json) if sc else None
    latest_version = sc.version if sc else None

    r_tl = await session.execute(
        select(SubtitleTimeline)
        .where(SubtitleTimeline.job_id == job_id)
        .order_by(SubtitleTimeline.id.desc())
        .limit(1)
    )
    st = r_tl.scalar_one_or_none()

    return JobDetailOut(
        job=JobOut.model_validate(job),
        latest_script=latest_script,
        latest_script_version=latest_version,
        audios=[AudioOutputBrief.model_validate(a) for a in job.audios],
        videos=[VideoOutputBrief.model_validate(v) for v in job.videos],
        subtitle_timeline_id=st.id if st else None,
    )


@router.get("/jobs/{job_id}/video/latest")
async def get_latest_video_file(job_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(VideoJob)
        .options(selectinload(VideoJob.videos))
        .where(VideoJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job or not job.videos:
        raise HTTPException(404, "视频尚未生成")
    latest = sorted(job.videos, key=lambda v: v.id, reverse=True)[0]
    path = (latest.file_path or "").strip()
    if not path:
        raise HTTPException(404, "视频路径为空")
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(str(p), media_type="video/mp4", filename=p.name)


@router.get("/jobs/{job_id}/video/latest/download")
async def download_latest_video_file(job_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(VideoJob)
        .options(selectinload(VideoJob.videos))
        .where(VideoJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job or not job.videos:
        raise HTTPException(404, "视频尚未生成")
    latest = sorted(job.videos, key=lambda v: v.id, reverse=True)[0]
    path = (latest.file_path or "").strip()
    if not path:
        raise HTTPException(404, "视频路径为空")
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(str(p), media_type="video/mp4", filename=f"job_{job_id}_latest.mp4")