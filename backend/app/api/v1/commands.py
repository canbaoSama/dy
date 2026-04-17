from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import CommandLog, JobAsset, JobStatus, NewsItem, ScriptRecord, SubtitleTimeline, VideoJob
from app.schemas import CommandRequest, CommandResponse, JobOut
from app.services.asset_download import download_binary
from app.services.command_parser import parse_command
from app.services.candidate_translate import translate_to_zh
from app.services.script_gen import rewrite_script_payload
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.content_extract import extract_article, fetch_media_candidates
from app.services.rss_ingest import ensure_default_sources
from app.api.v1.jobs import run_step_audio, run_step_render, run_step_script, run_step_subtitles

from app.api.v1.jobs import _run_pipeline_bg

router = APIRouter(tags=["commands"])


async def _safe_sync_sources(session: AsyncSession) -> None:
    try:
        await ensure_default_sources(session)
    except Exception:
        # 常见于 SQLite 被其他进程持锁；降级为沿用当前源配置，避免 /commands 直接 500。
        await session.rollback()

def _build_subtitle_draft_lines(item: NewsItem) -> list[str]:
    """从正文生成字幕草稿：按句切分，保留整句，避免原先每行硬截 70 字导致半句话。"""
    content = str(item.cleaned_content or "").strip()
    if not content:
        content = str(item.summary_one_liner or item.title or "").strip()
    if not content:
        return ["（正文暂不可用，请稍后重试）"]
    # 先按换行，再按中英文句号类切句
    pieces: list[str] = []
    for block in content.replace("\r", "\n").split("\n"):
        b = block.strip()
        if not b:
            continue
        for seg in re.split(r"(?<=[。！？!?])\s*", b):
            s = seg.strip()
            if s:
                pieces.append(s)
    if not pieces:
        pieces = [content.strip()[:400]]
    return pieces[:14]


async def _to_zh_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for x in lines:
        s = (x or "").strip()
        if not s:
            continue
        try:
            out.append((await translate_to_zh(s)).strip() or s)
        except Exception:
            out.append(s)
    return out


@router.post("/commands", response_model=CommandResponse)
async def chat_command(
    body: CommandRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    """自然语言运营入口（HTTP 适配器）；与 Telegram/飞书 Bot 共用同一语义，仅传输层不同。"""
    parsed = parse_command(body.message)
    session.add(CommandLog(raw_text=body.message, parsed_json=parsed))
    await session.commit()

    if parsed["kind"] == "candidates":
        # 每次查看候选前先对齐当前默认信源开关，避免看到历史旧来源
        await _safe_sync_sources(session)
        rows = await query_candidate_rows(session)
        items = await serialize_candidates(rows)
        lines = [
            f"{x.index}. 【{x.source}】{x.title_zh or x.title}（热度指数 {x.heat_index if x.heat_index is not None else '-'} · {x.tier}）\n   {x.url}"
            for x in items[:15]
        ]
        reply = (
            "今日全球热点候选（美东当天优先，仅统计当前启用来源；最多 15 条）：\n"
            + "\n".join(lines)
            if lines
            else "暂无候选，请先点「抓取新闻」拉取默认热榜组合（Google News + Reuters + BBC + Google Trends + Reddit）。"
        )
        return CommandResponse(reply=reply, candidates=items[:15])

    if parsed["kind"] == "make_job":
        await _safe_sync_sources(session)
        idx = int(parsed["index"])
        rows = await query_candidate_rows(session)
        if idx < 1 or idx > len(rows):
            return CommandResponse(reply="候选序号无效，先发「今天候选」查看列表。")
        item = rows[idx - 1]
        job = VideoJob(
            news_item_id=item.id,
            status=JobStatus.created.value,
            duration_sec=18,
            style_notes="快讯",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return CommandResponse(
            reply=(
                f"已创建任务 #{job.id}。\n"
                "现在进入人工编排模式：\n"
                "1) 发送「素材候选」查看可用素材\n"
                "2) 发送「选素材 1,3」将素材加入任务\n"
                "3) 再按步骤执行：生成脚本/生成音频/生成字幕/合成视频"
            ),
            active_job_id=job.id,
            job=JobOut.model_validate(job),
        )

    if parsed["kind"] == "asset_candidates":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）或指定 active_job_id。")
        job = await session.get(VideoJob, jid)
        if not job:
            return CommandResponse(reply="任务不存在。")
        job.status = JobStatus.extracting_content.value
        await session.commit()
        if not (job.news_item_id is None):
            item2 = await session.get(NewsItem, job.news_item_id)
            if item2 and not (item2.cleaned_content or "").strip():
                try:
                    await extract_article(session, item2)
                    await session.commit()
                except Exception:
                    pass
        job.status = JobStatus.collecting_assets.value
        await session.commit()
        item = await session.get(NewsItem, job.news_item_id)
        if not item or not (item.url or "").strip():
            return CommandResponse(reply="该任务缺少新闻 URL。")
        try:
            assets = await fetch_media_candidates(item.url)
        except Exception as e:
            return CommandResponse(reply=f"抓取素材候选失败：{e}", active_job_id=jid)
        if not assets:
            return CommandResponse(reply="未找到可用素材候选。", active_job_id=jid)
        lines = [
            f"{i+1}. [{('视频' if a.get('asset_type') == 'user_video' else '图片')}] {a.get('url')}"
            for i, a in enumerate(assets[:12])
        ]
        extracted_chars = len((item.cleaned_content or "").strip()) if item else 0
        extract_hint = (
            f"步骤 1/2：正文抽取完成（约 {extracted_chars} 字）。\n"
            if extracted_chars > 0
            else "步骤 1/2：正文抽取完成（正文不足时已使用摘要降级）。\n"
        )
        reply = (
            extract_hint
            + "步骤 2/2：素材候选如下（仅展示，需你确认后加入）：\n"
            + "\n".join(lines)
            + "\n\n发送「选素材 1,3」即可把第 1 和第 3 条加入任务素材。"
        )
        return CommandResponse(reply=reply, active_job_id=jid)

    if parsed["kind"] == "select_assets":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）或指定 active_job_id。")
        job = await session.get(VideoJob, jid)
        if not job:
            return CommandResponse(reply="任务不存在。")
        job.status = JobStatus.collecting_assets.value
        await session.commit()
        item = await session.get(NewsItem, job.news_item_id)
        if not item or not (item.url or "").strip():
            return CommandResponse(reply="该任务缺少新闻 URL。")
        idxs = [int(x) for x in (parsed.get("indices") or []) if int(x) > 0]
        if not idxs:
            return CommandResponse(reply="请选择素材序号，例如：选素材 1,3", active_job_id=jid)
        assets = await fetch_media_candidates(item.url)
        chosen = [assets[i - 1] for i in idxs if 1 <= i <= len(assets)]
        if not chosen:
            return CommandResponse(reply="素材序号无效，请先发送「素材候选」查看列表。", active_job_id=jid)
        r_old = await session.execute(
            select(JobAsset).where(
                JobAsset.job_id == jid,
                JobAsset.asset_type.in_(["user_image", "user_video"]),
            )
        )
        old_assets = list(r_old.scalars().all())
        for a in old_assets:
            meta = a.meta_json if isinstance(a.meta_json, dict) else {}
            src = str(meta.get("from") or "").strip()
            if src in {"chat_select", "remote_pick"}:
                await session.delete(a)
        await session.flush()
        save_dir = settings.assets_dir / f"job_{jid}" / "picked_remote"
        save_dir.mkdir(parents=True, exist_ok=True)
        existed = await session.scalar(select(func.count()).select_from(JobAsset).where(JobAsset.job_id == jid))
        base = int(existed or 0)
        added = 0
        for i, a in enumerate(chosen):
            url = str(a.get("url") or "").strip()
            t = str(a.get("asset_type") or "").strip() or "user_image"
            if t == "user_image":
                ext = Path(url.split("?")[0]).suffix.lower()
                if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                    ext = ".jpg"
                p = save_dir / f"picked_{base + i}{ext}"
                try:
                    await download_binary(url, p)
                except Exception:
                    continue
                session.add(
                    JobAsset(
                        job_id=jid,
                        asset_type="user_image",
                        local_path=str(p),
                        remote_url=url,
                        meta_json={"from": "chat_select"},
                    )
                )
                added += 1
            else:
                session.add(
                    JobAsset(
                        job_id=jid,
                        asset_type="user_video",
                        remote_url=url,
                        meta_json={"from": "chat_select"},
                    )
                )
                added += 1
        await session.commit()
        return CommandResponse(
            reply=f"已加入 {added} 个素材。已自动进入下一步：生成字幕。",
            active_job_id=jid,
        )
    if parsed["kind"] == "step_subtitle_draft":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        job = await session.get(VideoJob, int(jid))
        if not job:
            return CommandResponse(reply="任务不存在。")
        item = await session.get(NewsItem, job.news_item_id)
        if not item:
            return CommandResponse(reply="新闻不存在。")
        if not (item.cleaned_content or "").strip():
            try:
                await extract_article(session, item)
                await session.commit()
            except Exception:
                pass
        lines = await _to_zh_lines(_build_subtitle_draft_lines(item))
        job.status = JobStatus.generating_subtitles.value
        await session.commit()
        # 预览与对话框编辑共用：列出全部行，避免只显示前 6 条导致 /zimu 拉不到完整稿
        preview = "\n".join(f"- {x}" for x in lines)
        return CommandResponse(
            reply=(
                f"字幕步骤完成，任务：#{jid}，共 {len(lines)} 条。\n"
                f"字幕预览：\n{preview}\n"
                "你可以在对话框中直接编辑字幕并保存；确认后发送「确认字幕」继续。"
            ),
            active_job_id=int(jid),
        )


    if parsed["kind"] == "render":
        jid = parsed.get("job_id") or body.active_job_id
        if not jid:
            return CommandResponse(reply="请指定任务：先「做第 N 条」创建任务，或传入 active_job_id。")
        await run_step_render(int(jid), session)
        return CommandResponse(
            reply=f"已完成任务 #{jid} 的视频合成步骤。可在任务面板预览。",
            active_job_id=int(jid),
        )

    if parsed["kind"] == "step_script":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        r = await run_step_script(int(jid), session)
        p = r.get("payload") or {}
        titles = list(p.get("titles") or [])[:3]
        body_lines = list(p.get("body") or [])[:3]
        reply = (
            f"脚本步骤完成（v{r.get('version')}）。\n"
            f"Hook：{p.get('hook') or '—'}\n"
            + ("正文：\n" + "\n".join(f"- {x}" for x in body_lines) + "\n" if body_lines else "")
            + ("标题建议：\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles)) + "\n" if titles else "")
            + "下一步可执行：字幕时间轴。"
        )
        return CommandResponse(reply=reply, active_job_id=int(jid))

    if parsed["kind"] == "step_audio":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        r = await run_step_audio(int(jid), session)
        return CommandResponse(
            reply=(
                "音频步骤完成。\n"
                f"任务：#{jid}\n"
                f"时长：{r.get('duration_sec')}s\n"
                f"文件：{r.get('file_path')}\n"
                f"试听地址：/api/v1/jobs/{jid}/audio/latest\n"
                "如确认可发送「确认音频」继续合成视频。"
            ),
            active_job_id=int(jid),
        )

    if parsed["kind"] == "step_timeline":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        r = await run_step_subtitles(int(jid), session)
        r_tl = await session.execute(
            select(SubtitleTimeline).where(SubtitleTimeline.job_id == int(jid)).order_by(SubtitleTimeline.id.desc()).limit(1)
        )
        st = r_tl.scalar_one_or_none()
        cues = []
        if st and (st.timeline_json or "").strip():
            try:
                cues = json.loads(st.timeline_json)
            except Exception:
                cues = []
        preview_src = [str(x.get("text") or "").strip() for x in list(cues)[:4] if isinstance(x, dict)]
        preview_zh = await _to_zh_lines(preview_src)
        preview = "\n".join(f"- {x}" for x in preview_zh if x)
        return CommandResponse(
            reply=(
                f"字幕时间轴完成，任务：#{jid}，共 {r.get('count')} 条。\n"
                + (f"字幕预览：\n{preview}\n" if preview else "")
                + "下一步将进入配音合成。"
            ),
            active_job_id=int(jid),
        )

    if parsed["kind"] == "confirm_subtitles":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        job = await session.get(VideoJob, int(jid))
        if not job:
            return CommandResponse(reply="任务不存在。")
        job.status = JobStatus.generating_subtitles.value
        await session.commit()
        return CommandResponse(
            reply=(
                f"字幕已确认（任务：#{jid}）。\n"
                "即将继续：脚本生成 -> 字幕时间轴 -> 配音合成。"
            ),
            active_job_id=int(jid),
        )

    if parsed["kind"] == "confirm_audio":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        r = await run_step_render(int(jid), session)
        return CommandResponse(
            reply=(
                "视频合成完成。\n"
                f"任务：#{jid}\n"
                f"视频文件：{r.get('video_path')}\n"
                f"预览地址：/api/v1/jobs/{jid}/video/latest\n"
                f"下载地址：/api/v1/jobs/{jid}/video/latest/download"
            ),
            active_job_id=int(jid),
        )

    if parsed["kind"] == "step_render":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务（做第 N 条）。")
        r = await run_step_render(int(jid), session)
        return CommandResponse(
            reply=(
                "视频合成完成。\n"
                f"任务：#{jid}\n"
                f"视频文件：{r.get('video_path')}\n"
                f"预览地址：/api/v1/jobs/{jid}/video/latest\n"
                f"下载地址：/api/v1/jobs/{jid}/video/latest/download"
            ),
            active_job_id=int(jid),
        )

    if parsed["kind"] == "rewrite_script":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先创建任务并指定 active_job_id，或在同一会话中先「做第 N 条」。")
        job = await session.get(VideoJob, jid)
        if not job:
            return CommandResponse(reply="任务不存在。")
        r = await session.execute(
            select(ScriptRecord).where(ScriptRecord.job_id == jid).order_by(ScriptRecord.version.desc()).limit(1)
        )
        sc = r.scalar_one_or_none()
        if not sc:
            return CommandResponse(reply="还没有脚本，请先跑一遍渲染管线生成脚本。")
        prev = json.loads(sc.payload_json)
        new_payload = rewrite_script_payload(prev, str(parsed.get("instruction", "")))
        prev_n = await session.scalar(
            select(func.count()).select_from(ScriptRecord).where(ScriptRecord.job_id == jid)
        )
        ver = int(prev_n or 0) + 1
        session.add(
            ScriptRecord(
                job_id=jid,
                version=ver,
                payload_json=json.dumps(new_payload, ensure_ascii=False),
            )
        )
        await session.commit()
        titles = new_payload.get("titles") or []
        return CommandResponse(
            reply="已根据指令生成新版本脚本。标题建议：\n" + "\n".join(f"- {t}" for t in titles[:3]),
            active_job_id=jid,
        )

    if parsed["kind"] == "titles":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先指定 active_job_id（当前任务）。")
        r = await session.execute(
            select(ScriptRecord).where(ScriptRecord.job_id == jid).order_by(ScriptRecord.version.desc()).limit(1)
        )
        sc = r.scalar_one_or_none()
        if not sc:
            return CommandResponse(reply="暂无脚本。")
        pl = json.loads(sc.payload_json)
        titles = pl.get("titles") or []
        return CommandResponse(
            reply="标题建议：\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles)),
            active_job_id=jid,
        )

    if parsed["kind"] == "intro":
        jid = body.active_job_id
        if not jid:
            return CommandResponse(reply="请先指定 active_job_id。")
        r = await session.execute(
            select(ScriptRecord).where(ScriptRecord.job_id == jid).order_by(ScriptRecord.version.desc()).limit(1)
        )
        sc = r.scalar_one_or_none()
        if not sc:
            return CommandResponse(reply="暂无脚本。")
        pl = json.loads(sc.payload_json)
        intro = " / ".join(pl.get("cover_texts") or []) + "\n评论引导：" + str(pl.get("comment_prompt", ""))
        return CommandResponse(reply=f"简介建议：\n{intro}", active_job_id=jid)

    return CommandResponse(
        reply='可尝试：「今天候选」「做第 2 条」「开始渲染」。也可先 POST /ingest/trigger 抓取新闻。',
    )
