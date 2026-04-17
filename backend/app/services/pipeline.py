"""单条任务生产管线（MVP）：与完整方案状态机对齐，步骤可拆 worker。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import feedparser
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AudioOutput,
    JobAsset,
    JobStatus,
    NewsItem,
    ScriptRecord,
    SubtitleTimeline,
    VideoJob,
    VideoOutput,
)
from app.services.asset_download import download_binary
from app.services.content_extract import extract_article
from app.services.render_stub import render_video_stub
from app.services.script_gen import generate_script_payload, rewrite_script_payload
from app.services.subtitle_build import build_stub_timeline
from app.services.tts_stub import synthesize_narration


def _publish_latest_video(job_id: int, video_path: str | None) -> str | None:
    """将最新成片同步到项目根 videos/job_{id}_latest.mp4。"""
    src = Path((video_path or "").strip())
    if not src.exists():
        return None
    videos_dir = settings.data_dir.parent.parent / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    dest = videos_dir / f"job_{job_id}_latest.mp4"
    shutil.copy2(src, dest)
    return str(dest)


async def _fallback_hero_from_rss(item: NewsItem) -> str | None:
    """正文 403 时，回退到 RSS entry 的 media image。"""
    source = item.source
    if not source or not source.rss_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(source.rss_url, headers={"User-Agent": "OverseasNewsFactory/0.1"})
            r.raise_for_status()
        parsed = feedparser.parse(r.text)
        target = (item.url or "").strip()
        for entry in parsed.entries[:80]:
            link = str(entry.get("link") or entry.get("id") or "").strip()
            if not link or link != target:
                continue
            media = entry.get("media_content") or []
            if isinstance(media, list):
                for m in media:
                    u = str((m or {}).get("url") or "").strip()
                    if u.startswith(("http://", "https://")):
                        return u
            thumb = entry.get("media_thumbnail") or []
            if isinstance(thumb, list):
                for m in thumb:
                    u = str((m or {}).get("url") or "").strip()
                    if u.startswith(("http://", "https://")):
                        return u
    except Exception:
        return None
    return None


async def run_job_pipeline(session: AsyncSession, job_id: int, options: dict | None = None) -> VideoJob:
    result = await session.execute(
        select(VideoJob)
        .options(selectinload(VideoJob.news_item).selectinload(NewsItem.source))
        .where(VideoJob.id == job_id)
    )
    job = result.scalar_one()
    item = job.news_item

    tracker: dict[str, str | None] = {"last": None}
    opts = options or {}

    async def advance(st: JobStatus) -> None:
        tracker["last"] = st.value
        job.status = st.value
        await session.commit()
        await session.refresh(job)

    try:
        job.failed_stage = None
        job.error_message = None

        await advance(JobStatus.extracting_content)

        if not item.cleaned_content:
            try:
                await extract_article(session, item)
                await session.commit()
                await session.refresh(item)
            except Exception:
                # 某些站点会 403/反爬；MVP 继续用 RSS 标题/摘要降级生成，避免整条任务中断。
                base = (item.summary_one_liner or item.title or "").strip()
                item.cleaned_content = base[:1200] if base else "来源站点正文暂不可用，已按标题和摘要继续生成。"
                await session.commit()

        await advance(JobStatus.scoring_candidate)

        await advance(JobStatus.generating_script)

        # 新闻简报默认压到短时长，避免冗长口播。
        req_duration = opts.get("duration_sec")
        base_duration = int(req_duration) if req_duration is not None else int(job.duration_sec or 18)
        target_duration = max(12, min(base_duration, 22))
        if isinstance(opts.get("style_notes"), str) and opts.get("style_notes"):
            job.style_notes = str(opts.get("style_notes"))
        job.duration_sec = target_duration
        await session.commit()

        payload = await generate_script_payload(session, item, duration_sec=target_duration, style=job.style_notes or "快讯")
        instruction = str(opts.get("instruction") or "").strip()
        if instruction:
            payload = rewrite_script_payload(payload, instruction)
        prev_n = await session.scalar(
            select(func.count()).select_from(ScriptRecord).where(ScriptRecord.job_id == job.id)
        )
        ver = int(prev_n or 0) + 1
        session.add(
            ScriptRecord(
                job_id=job.id,
                version=ver,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )
        await session.commit()

        await advance(JobStatus.collecting_assets)

        job_dir = settings.outputs_dir / f"job_{job.id}"
        job_dir.mkdir(parents=True, exist_ok=True)

        if not item.hero_image_url:
            item.hero_image_url = await _fallback_hero_from_rss(item)
            await session.commit()

        if item.hero_image_url:
            ext = ".jpg"
            hero_path = settings.assets_dir / f"job_{job.id}" / f"hero{ext}"
            try:
                await download_binary(item.hero_image_url, hero_path, referer=item.url)
                session.add(
                    JobAsset(
                        job_id=job.id,
                        asset_type="hero_image",
                        local_path=str(hero_path),
                        remote_url=item.hero_image_url,
                        meta_json={"role": "hero"},
                    )
                )
            except Exception:
                session.add(
                    JobAsset(
                        job_id=job.id,
                        asset_type="hero_image",
                        remote_url=item.hero_image_url,
                        meta_json={"error": "download_failed"},
                    )
                )
            await session.commit()

        await advance(JobStatus.generating_audio)

        narration_text = "\n".join(
            [payload.get("hook", "")]
            + list(payload.get("body") or [])
            + [payload.get("ending", "")]
        )
        tts_voice = str(opts.get("tts_voice") or "").strip() or None
        audio_path, duration_sec = await synthesize_narration(
            narration_text,
            job_dir,
            target_duration=target_duration,
            voice=tts_voice,
        )
        session.add(
            AudioOutput(
                job_id=job.id,
                file_path=audio_path,
                duration_sec=duration_sec,
                meta_json={"provider": settings.tts_provider, "voice": tts_voice},
            )
        )
        await session.commit()

        await advance(JobStatus.building_timeline)

        timeline = build_stub_timeline(narration_text, duration_sec=duration_sec)
        timeline_str = json.dumps(timeline, ensure_ascii=False, indent=2)
        tl_path = job_dir / "subtitles.json"
        tl_path.write_text(timeline_str, encoding="utf-8")
        session.add(
            JobAsset(
                job_id=job.id,
                asset_type="subtitle_timeline",
                local_path=str(tl_path),
                meta_json={"format": "stub"},
            )
        )
        session.add(
            SubtitleTimeline(
                job_id=job.id,
                provider="stub",
                timeline_json=timeline_str,
            )
        )
        await session.commit()

        await advance(JobStatus.rendering_video)

        r_user_assets = await session.execute(
            select(JobAsset).where(
                JobAsset.job_id == job.id,
                JobAsset.asset_type.in_(["user_image", "user_video"]),
            )
        )
        user_assets = list(r_user_assets.scalars().all())
        user_image_paths = [a.local_path for a in user_assets if a.asset_type == "user_image" and a.local_path]
        user_video_paths = [a.local_path for a in user_assets if a.asset_type == "user_video" and a.local_path]
        user_video_urls = [a.remote_url for a in user_assets if a.asset_type == "user_video" and a.remote_url]

        # 成片时长应与配音实际长度一致；target_duration 仅用于脚本/口播压缩目标，不能拿来裁视频。
        video_duration_sec = max(10.0, min(float(duration_sec) + 0.4, 90.0))

        video_path, preview_path = await render_video_stub(
            job_dir,
            {
                "script": payload,
                "duration_sec": video_duration_sec,
                "source": item.source.name if item.source else "",
                "hero_image_url": item.hero_image_url,
                "page_screenshot_path": item.page_screenshot_path,
                "article_url": item.url,
                "title": item.title,
                "summary": item.summary_one_liner,
                "narration_text": narration_text,
                "audio_path": audio_path,
                "user_image_paths": user_image_paths,
                "user_video_paths": user_video_paths,
                "user_video_urls": user_video_urls,
                "must_use_uploaded_assets": bool(opts.get("must_use_uploaded_assets", False)),
                "prefer_video_assets": bool(opts.get("prefer_video_assets", False)),
                "subtitle_tone": str(opts.get("subtitle_tone") or "").strip() or None,
                "aspect_ratio": str(opts.get("aspect_ratio") or "").strip() or "9:16",
            },
        )
        published_latest = _publish_latest_video(job.id, video_path)
        session.add(
            VideoOutput(
                job_id=job.id,
                file_path=video_path,
                preview_path=preview_path,
                meta_json={
                    "titles": payload.get("titles"),
                    "cover_texts": payload.get("cover_texts"),
                    "comment_prompt": payload.get("comment_prompt"),
                    "published_latest": published_latest,
                    "publish_hint": "完整方案在此回传抖音标题/简介/评论引导",
                },
            )
        )
        await session.commit()

        job.failed_stage = None
        job.error_message = None
        await advance(JobStatus.ready_for_review)

    except Exception as e:  # noqa: BLE001
        job.failed_stage = tracker["last"]
        job.status = JobStatus.failed.value
        job.error_message = str(e)[:2000]
        await session.commit()
        await session.refresh(job)

    return job


def script_to_narration_text(payload: dict) -> str:
    parts = [payload.get("hook", "")]
    parts.extend(list(payload.get("body") or []))
    parts.append(payload.get("ending", ""))
    return "\n".join(p for p in parts if p)
