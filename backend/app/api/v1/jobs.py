from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import JobAsset, JobStatus, NewsItem, ScriptRecord, SubtitleTimeline, VideoJob
from app.schemas import AudioOutputBrief, JobDetailOut, JobOut, VideoOutputBrief
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.pipeline import run_job_pipeline
from app.services.tts_stub import synthesize_preview_mp3

router = APIRouter(tags=["jobs"])


class RerenderRequest(BaseModel):
    instruction: str | None = None
    duration_sec: int | None = None
    style_notes: str | None = None
    must_use_uploaded_assets: bool | None = None
    prefer_video_assets: bool | None = None
    subtitle_tone: str | None = None
    tts_voice: str | None = None


class TtsPreviewRequest(BaseModel):
    voice: str
    text: str | None = None


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


async def _run_pipeline_bg_with_options(job_id: int, options: dict) -> None:
    async with SessionLocal() as session:
        await run_job_pipeline(session, job_id, options=options)


@router.post("/jobs/{job_id}/pipeline")
async def trigger_pipeline(job_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_pipeline_bg, job_id)
    return {"ok": True, "job_id": job_id, "note": "管线异步执行，请轮询 GET /jobs/{id} 或 GET /jobs/{id}/detail"}


@router.post("/jobs/{job_id}/rerender")
async def trigger_rerender(job_id: int, body: RerenderRequest, background_tasks: BackgroundTasks):
    options = body.model_dump(exclude_none=True)
    background_tasks.add_task(_run_pipeline_bg_with_options, job_id, options)
    return {
        "ok": True,
        "job_id": job_id,
        "note": "已按新需求重生成，请轮询任务详情",
        "options": options,
    }


@router.post("/tts/preview")
async def preview_tts_voice(body: TtsPreviewRequest):
    voice = (body.voice or "").strip()
    if not voice:
        raise HTTPException(400, "voice 不能为空")
    sample_text = (body.text or "").strip() or "你好，这是一段音色试听。"
    p = settings.outputs_dir / "_tts_preview" / f"{voice}.mp3"
    try:
        await synthesize_preview_mp3(voice, sample_text, p)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"试听生成失败：{e}") from e
    return FileResponse(str(p), media_type="audio/mpeg", filename=f"{voice}.mp3")


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
    latest_script = None
    latest_version = sc.version if sc else None
    if sc and (sc.payload_json or "").strip():
        try:
            latest_script = json.loads(sc.payload_json)
        except Exception:
            latest_script = None

    r_tl = await session.execute(
        select(SubtitleTimeline)
        .where(SubtitleTimeline.job_id == job_id)
        .order_by(SubtitleTimeline.id.desc())
        .limit(1)
    )
    st = r_tl.scalar_one_or_none()

    item = await session.get(NewsItem, job.news_item_id)
    news_title = (item.title or None) if item else None
    content_chars = len(item.cleaned_content or "") if item and item.cleaned_content else None
    candidate_score_10 = None
    candidate_tier = (item.candidate_tier or None) if item else None
    if item and isinstance(item.score_json, dict):
        try:
            raw_total = float(item.score_json.get("total") or 0.0)
            if raw_total:
                candidate_score_10 = round(max(0.0, min(raw_total * 10.0 / 7.0, 10.0)), 1)
        except Exception:
            pass

    subtitle_cues: list | None = None
    if st and (st.timeline_json or "").strip():
        try:
            raw_tl = json.loads(st.timeline_json)
            if isinstance(raw_tl, list):
                subtitle_cues = [x for x in raw_tl if isinstance(x, dict)]
            elif isinstance(raw_tl, dict):
                subtitle_cues = [raw_tl]
            else:
                subtitle_cues = None
        except Exception:
            subtitle_cues = None

    return JobDetailOut(
        job=JobOut.model_validate(job),
        latest_script=latest_script,
        latest_script_version=latest_version,
        audios=[AudioOutputBrief.model_validate(a) for a in job.audios],
        videos=[VideoOutputBrief.model_validate(v) for v in job.videos],
        subtitle_timeline_id=st.id if st else None,
        news_title=news_title,
        content_chars=content_chars,
        candidate_score_10=candidate_score_10,
        candidate_tier=candidate_tier,
        subtitle_cues=subtitle_cues,
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