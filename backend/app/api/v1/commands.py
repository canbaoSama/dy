from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CommandLog, JobStatus, ScriptRecord, VideoJob
from app.schemas import CommandRequest, CommandResponse, JobOut
from app.services.command_parser import parse_command
from app.services.script_gen import rewrite_script_payload
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.rss_ingest import ensure_default_sources

from app.api.v1.jobs import _run_pipeline_bg

router = APIRouter(tags=["commands"])


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
        await ensure_default_sources(session)
        rows = await query_candidate_rows(session)
        items = await serialize_candidates(rows)
        lines = [
            f"{x.index}. 【{x.source}】{x.title_zh or x.title}（热度指数 {x.heat_index if x.heat_index is not None else '-'} · {x.tier}）"
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
        await ensure_default_sources(session)
        idx = int(parsed["index"])
        rows = await query_candidate_rows(session)
        if idx < 1 or idx > len(rows):
            return CommandResponse(reply="候选序号无效，先发「今天候选」查看列表。")
        item = rows[idx - 1]
        job = VideoJob(
            news_item_id=item.id,
            status=JobStatus.created.value,
            duration_sec=35,
            style_notes="快讯",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return CommandResponse(
            reply=f"已创建任务 #{job.id}。发送「开始渲染」或调用 POST /api/v1/jobs/{job.id}/pipeline 开始生产。",
            active_job_id=job.id,
            job=JobOut.model_validate(job),
        )

    if parsed["kind"] == "render":
        jid = parsed.get("job_id") or body.active_job_id
        if not jid:
            return CommandResponse(reply="请指定任务：先「做第 N 条」创建任务，或传入 active_job_id。")
        background_tasks.add_task(_run_pipeline_bg, int(jid))
        return CommandResponse(
            reply=f"已开始执行任务 #{jid} 的生产管线（异步）。可 GET /api/v1/jobs/{jid}/detail 查看进度与产物。",
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
