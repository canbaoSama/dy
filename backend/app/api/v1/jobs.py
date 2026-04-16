from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import AudioOutput, JobAsset, JobStatus, NewsItem, ScriptRecord, SubtitleTimeline, VideoJob, VideoOutput
from app.schemas import AudioOutputBrief, JobDetailOut, JobOut, VideoOutputBrief
from app.services.asset_download import download_binary
from app.services.content_extract import extract_article, fetch_media_candidates
from app.services.job_maintenance import recover_stuck_jobs
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.pipeline import run_job_pipeline
from app.services.render_stub import render_video_stub
from app.services.script_gen import generate_script_payload
from app.services.subtitle_build import build_stub_timeline
from app.services.tts_stub import synthesize_preview_mp3
from app.services.tts_stub import synthesize_narration

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


class PickRemoteAssetItem(BaseModel):
    url: str
    asset_type: str | None = None


class PickRemoteAssetsRequest(BaseModel):
    items: list[PickRemoteAssetItem]


class SubtitleCueIn(BaseModel):
    start: float
    end: float
    text: str


class UpdateSubtitlesRequest(BaseModel):
    cues: list[SubtitleCueIn]


def _script_to_narration_text(payload: dict) -> str:
    parts = [payload.get("hook", "")]
    parts.extend(list(payload.get("body") or []))
    parts.append(payload.get("ending", ""))
    return "\n".join(str(p or "") for p in parts if str(p or "").strip())


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
async def trigger_pipeline(
    job_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    # 每次触发新任务前先清理历史卡死任务，避免旧任务长期占据中间态。
    await recover_stuck_jobs(session, stale_minutes=8)
    background_tasks.add_task(_run_pipeline_bg, job_id)
    return {"ok": True, "job_id": job_id, "note": "管线异步执行，请轮询 GET /jobs/{id} 或 GET /jobs/{id}/detail"}


@router.post("/jobs/{job_id}/rerender")
async def trigger_rerender(
    job_id: int,
    body: RerenderRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    await recover_stuck_jobs(session, stale_minutes=8)
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


@router.get("/jobs/{job_id}/asset-candidates")
async def get_job_asset_candidates(
    job_id: int,
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    item = await session.get(NewsItem, job.news_item_id)
    if not item or not (item.url or "").strip():
        raise HTTPException(400, "该任务缺少新闻 URL")
    try:
        candidates = await fetch_media_candidates(item.url)
    except Exception as e:
        raise HTTPException(502, f"抓取候选素材失败：{e}") from e
    return {"job_id": job_id, "article_url": item.url, "items": candidates}


@router.post("/jobs/{job_id}/assets/select-remote")
async def pick_remote_assets(
    job_id: int,
    body: PickRemoteAssetsRequest,
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    if not body.items:
        raise HTTPException(400, "items 不能为空")

    save_dir = settings.assets_dir / f"job_{job_id}" / "picked_remote"
    save_dir.mkdir(parents=True, exist_ok=True)
    existing_count = await session.scalar(select(func.count()).select_from(JobAsset).where(JobAsset.job_id == job_id))
    base = int(existing_count or 0)
    added: list[dict[str, str]] = []
    for idx, it in enumerate(body.items):
        u = (it.url or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        t = (it.asset_type or "").strip().lower()
        if t not in {"user_image", "user_video"}:
            low = u.lower()
            t = "user_video" if any(k in low for k in (".mp4", ".m3u8", ".webm")) else "user_image"
        if t == "user_image":
            ext = Path(u.split("?")[0]).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                ext = ".jpg"
            local = save_dir / f"picked_{base + idx}{ext}"
            try:
                await download_binary(u, local)
            except Exception:
                continue
            session.add(
                JobAsset(
                    job_id=job_id,
                    asset_type="user_image",
                    local_path=str(local),
                    remote_url=u,
                    meta_json={"from": "remote_pick"},
                )
            )
            added.append({"asset_type": "user_image", "url": u, "local_path": str(local)})
        else:
            session.add(
                JobAsset(
                    job_id=job_id,
                    asset_type="user_video",
                    remote_url=u,
                    meta_json={"from": "remote_pick"},
                )
            )
            added.append({"asset_type": "user_video", "url": u, "local_path": ""})

    if not added:
        raise HTTPException(400, "没有可加入的素材")
    await session.commit()
    return {"ok": True, "job_id": job_id, "added": len(added), "items": added}


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


@router.get("/jobs/{job_id}/audio/latest")
async def get_latest_audio_file(job_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(VideoJob)
        .options(selectinload(VideoJob.audios))
        .where(VideoJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job or not job.audios:
        raise HTTPException(404, "音频尚未生成")
    latest = sorted(job.audios, key=lambda a: a.id, reverse=True)[0]
    path = (latest.file_path or "").strip()
    if not path:
        raise HTTPException(404, "音频路径为空")
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "音频文件不存在")
    return FileResponse(str(p), media_type="audio/mpeg", filename=p.name)


@router.put("/jobs/{job_id}/subtitles/latest")
async def update_latest_subtitles(
    job_id: int,
    body: UpdateSubtitlesRequest,
    session: AsyncSession = Depends(get_db),
):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    cues = []
    for idx, cue in enumerate(body.cues):
        text = (cue.text or "").strip()
        if not text:
            continue
        start = max(0.0, float(cue.start))
        end = max(start, float(cue.end))
        cues.append({"index": idx + 1, "start": start, "end": end, "text": text})
    if not cues:
        raise HTTPException(400, "字幕不能为空")
    timeline_str = json.dumps(cues, ensure_ascii=False, indent=2)
    job_dir = settings.outputs_dir / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    tl_path = job_dir / "subtitles.json"
    tl_path.write_text(timeline_str, encoding="utf-8")
    session.add(
        SubtitleTimeline(
            job_id=job_id,
            provider="manual_edit",
            timeline_json=timeline_str,
        )
    )
    session.add(
        JobAsset(
            job_id=job_id,
            asset_type="subtitle_timeline",
            local_path=str(tl_path),
            meta_json={"format": "manual_edit"},
        )
    )
    await session.commit()
    return {"ok": True, "job_id": job_id, "count": len(cues)}


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


@router.post("/jobs/repair-stuck")
async def repair_stuck_jobs(
    stale_minutes: int = Query(8, ge=1, le=120),
    session: AsyncSession = Depends(get_db),
):
    result = await recover_stuck_jobs(
        session,
        stale_minutes=stale_minutes,
        reason_prefix="手动触发修复：任务卡住，已标记失败，可直接重试",
    )
    return {"ok": True, **result, "stale_minutes": stale_minutes}


@router.post("/jobs/{job_id}/steps/script")
async def run_step_script(job_id: int, session: AsyncSession = Depends(get_db)):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    item = await session.get(NewsItem, job.news_item_id)
    if not item:
        raise HTTPException(404, "news_item 不存在")
    if not item.cleaned_content:
        try:
            await extract_article(session, item)
            await session.commit()
        except Exception:
            base = (item.summary_one_liner or item.title or "").strip()
            item.cleaned_content = base[:1200] if base else "正文暂不可用"
            await session.commit()
    payload = await generate_script_payload(session, item, duration_sec=int(job.duration_sec or 18), style=job.style_notes or "快讯")
    prev_n = await session.scalar(select(func.count()).select_from(ScriptRecord).where(ScriptRecord.job_id == job_id))
    ver = int(prev_n or 0) + 1
    session.add(
        ScriptRecord(
            job_id=job_id,
            version=ver,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )
    job.status = JobStatus.generating_script.value
    await session.commit()
    return {"ok": True, "job_id": job_id, "step": "script", "version": ver, "payload": payload}


@router.post("/jobs/{job_id}/steps/audio")
async def run_step_audio(job_id: int, session: AsyncSession = Depends(get_db)):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    r_sc = await session.execute(
        select(ScriptRecord).where(ScriptRecord.job_id == job_id).order_by(ScriptRecord.version.desc()).limit(1)
    )
    sc = r_sc.scalar_one_or_none()
    if not sc:
        raise HTTPException(400, "请先执行脚本步骤")
    payload = json.loads(sc.payload_json)
    narration_text = _script_to_narration_text(payload)
    job_dir = settings.outputs_dir / f"job_{job_id}"
    audio_path, duration_sec = await synthesize_narration(narration_text, job_dir, target_duration=float(job.duration_sec or 18))
    session.add(
        AudioOutput(
            job_id=job_id,
            file_path=audio_path,
            duration_sec=duration_sec,
            meta_json={"provider": settings.tts_provider},
        )
    )
    job.status = JobStatus.generating_audio.value
    await session.commit()
    return {"ok": True, "job_id": job_id, "step": "audio", "file_path": audio_path, "duration_sec": duration_sec}


@router.post("/jobs/{job_id}/steps/subtitles")
async def run_step_subtitles(job_id: int, session: AsyncSession = Depends(get_db)):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    r_sc = await session.execute(
        select(ScriptRecord).where(ScriptRecord.job_id == job_id).order_by(ScriptRecord.version.desc()).limit(1)
    )
    sc = r_sc.scalar_one_or_none()
    if not sc:
        raise HTTPException(400, "请先执行脚本步骤")
    payload = json.loads(sc.payload_json)
    narration_text = _script_to_narration_text(payload)
    r_au = await session.execute(
        select(AudioOutput).where(AudioOutput.job_id == job_id).order_by(AudioOutput.id.desc()).limit(1)
    )
    au = r_au.scalar_one_or_none()
    duration = float((au.duration_sec if au else None) or float(job.duration_sec or 18))
    timeline = build_stub_timeline(narration_text, duration_sec=duration)
    timeline_str = json.dumps(timeline, ensure_ascii=False, indent=2)
    job_dir = settings.outputs_dir / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    tl_path = job_dir / "subtitles.json"
    tl_path.write_text(timeline_str, encoding="utf-8")
    session.add(
        JobAsset(
            job_id=job_id,
            asset_type="subtitle_timeline",
            local_path=str(tl_path),
            meta_json={"format": "stub"},
        )
    )
    session.add(
        SubtitleTimeline(
            job_id=job_id,
            provider="stub",
            timeline_json=timeline_str,
        )
    )
    job.status = JobStatus.building_timeline.value
    await session.commit()
    return {"ok": True, "job_id": job_id, "step": "subtitles", "count": len(timeline)}


@router.post("/jobs/{job_id}/steps/render")
async def run_step_render(job_id: int, session: AsyncSession = Depends(get_db)):
    job = await session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    item = await session.get(NewsItem, job.news_item_id)
    if not item:
        raise HTTPException(404, "news_item 不存在")
    r_sc = await session.execute(
        select(ScriptRecord).where(ScriptRecord.job_id == job_id).order_by(ScriptRecord.version.desc()).limit(1)
    )
    sc = r_sc.scalar_one_or_none()
    if not sc:
        raise HTTPException(400, "请先执行脚本步骤")
    payload = json.loads(sc.payload_json)
    r_au = await session.execute(
        select(AudioOutput).where(AudioOutput.job_id == job_id).order_by(AudioOutput.id.desc()).limit(1)
    )
    au = r_au.scalar_one_or_none()
    if not au:
        raise HTTPException(400, "请先执行音频步骤")
    r_assets = await session.execute(
        select(JobAsset).where(
            JobAsset.job_id == job_id,
            JobAsset.asset_type.in_(["user_image", "user_video"]),
        )
    )
    assets = list(r_assets.scalars().all())
    user_image_paths = [a.local_path for a in assets if a.asset_type == "user_image" and a.local_path]
    user_video_paths = [a.local_path for a in assets if a.asset_type == "user_video" and a.local_path]
    user_video_urls = [a.remote_url for a in assets if a.asset_type == "user_video" and a.remote_url]
    job_dir = settings.outputs_dir / f"job_{job_id}"
    narration_text = _script_to_narration_text(payload)
    video_path, preview_path = await render_video_stub(
        job_dir,
        {
            "script": payload,
            "duration_sec": int(job.duration_sec or 18),
            "source": "",
            "hero_image_url": item.hero_image_url,
            "page_screenshot_path": item.page_screenshot_path,
            "article_url": item.url,
            "title": item.title,
            "summary": item.summary_one_liner,
            "narration_text": narration_text,
            "audio_path": au.file_path,
            "user_image_paths": user_image_paths,
            "user_video_paths": user_video_paths,
            "user_video_urls": user_video_urls,
            "must_use_uploaded_assets": bool(len(user_image_paths) or len(user_video_paths) or len(user_video_urls)),
            "prefer_video_assets": True,
        },
    )
    session.add(
        VideoOutput(
            job_id=job_id,
            file_path=video_path,
            preview_path=preview_path,
            meta_json={"step_mode": True},
        )
    )
    job.status = JobStatus.rendering_video.value
    await session.commit()
    job.status = JobStatus.ready_for_review.value
    await session.commit()
    return {"ok": True, "job_id": job_id, "step": "render", "video_path": video_path}