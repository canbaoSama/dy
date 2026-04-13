"""中文快讯脚本生成：OpenAI 兼容 API 或本地 mock（对应文档 JSON 结构）。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import NewsItem, NewsSource


def _mock_script(item: NewsItem, duration_sec: int, style: str, source_name: str = "") -> dict[str, Any]:
    title = item.title
    snippet = (item.summary_one_liner or item.cleaned_content or "")[:400]
    core = snippet[:90] if snippet else title[:90]
    point_b = snippet[90:180] if len(snippet) > 90 else ""
    point_c = snippet[180:270] if len(snippet) > 180 else ""

    return {
        "hook": f"{source_name or '海外媒体'}最新消息：{title[:28]}",
        "body": [
            f"{core}" if core else f"{title[:40]}",
            f"{point_b}" if point_b else "目前公开信息仍在更新中。",
            f"{point_c}" if point_c else "后续以官方与原文更新为准。",
        ],
        "ending": "以上是这条新闻的最新进展。",
        "titles": [
            f"{title[:28]}",
            f"海外突发：{title[:20]}",
            f"一分钟看懂：{title[:18]}",
        ],
        "cover_texts": ["海外快讯", "一分钟看懂"],
        "comment_prompt": "",
        "meta": {"duration_sec": duration_sec, "style": style, "provider": "mock"},
    }


async def generate_script_payload(
    session: AsyncSession,
    item: NewsItem,
    duration_sec: int = 18,
    style: str = "快讯",
) -> dict[str, Any]:
    src_row = await session.get(NewsSource, item.source_id)
    source_name = src_row.name if src_row else ""
    if settings.openai_api_key:
        try:
            return await _openai_script(item, duration_sec, style)
        except Exception:
            pass
    return _mock_script(item, duration_sec, style, source_name=source_name)


async def _openai_script(item: NewsItem, duration_sec: int, style: str) -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    text = f"标题: {item.title}\n摘要: {item.summary_one_liner or ''}\n正文节选: {(item.cleaned_content or '')[:6000]}"
    sys = (
        "你是新闻短视频编辑。输出严格 JSON，键：hook, body(字符串数组3条), ending, titles(3条中文标题), "
        "cover_texts(2条短封面字), comment_prompt。要求：只写新闻内容，精简客观，不要出现“要素一/要素二”字样，"
        "不要评论区引导、不要点赞收藏引导。"
        f"总时长约{duration_sec}秒，风格：{style}。"
    )
    resp = await client.chat.completions.create(
        model=settings.script_model,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": text},
        ],
        temperature=0.6,
    )
    raw = resp.choices[0].message.content or "{}"
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    data["comment_prompt"] = ""
    data["meta"] = {"duration_sec": duration_sec, "style": style, "provider": "openai"}
    return data


def rewrite_script_payload(
    prev: dict[str, Any],
    instruction: str,
) -> dict[str, Any]:
    """预留：多轮改写（爆点/口语）；MVP 用简单字符串拼接提示。"""
    out = dict(prev)
    ins = instruction.strip()
    if "炸" in ins or "钩子" in ins:
        out["hook"] = f"【更抓人】{out.get('hook', '')}"[:200]
    if "口语" in ins or "别像新闻" in ins or "联播" in ins:
        out["body"] = [f"说白了：{b}"[:300] for b in (out.get("body") or [])]
    if "普通人" in ins or "意味着什么" in ins:
        out["body"] = (out.get("body") or []) + ["对普通人来说：值得留意后续进展，不必过度焦虑。"]
    out.setdefault("meta", {})["rewrite_note"] = ins
    return out
