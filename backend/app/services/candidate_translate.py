from __future__ import annotations

import asyncio
import re
from typing import Optional

from app.config import settings

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")

# 并发翻译时限制出站连接，避免公共接口限流
_translate_sem = asyncio.Semaphore(8)


def _has_zh(text: str) -> bool:
    return bool(_ZH_RE.search(text))


def _mymemory_out_valid(q: str, out: str) -> bool:
    if not out or not out.strip():
        return False
    u = out.upper()
    if "INVALID SOURCE LANGUAGE" in u or "QUERY LENGTH LIMIT" in u:
        return False
    if out.strip().lower() == q.strip().lower():
        return False
    return True


async def _translate_youdao(raw: str) -> Optional[str]:
    """有道词典网页接口，国内网络通常可达；适合英文标题/摘要整句翻译。"""
    q = (raw or "").strip()[:500]
    if len(q) < 2:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://dict.youdao.com/jsonapi_s",
                params={"q": q, "doctype": "json", "jsonversion": "4"},
                headers={"User-Agent": "Mozilla/5.0 (compatible; overseas-news-factory/1.0)"},
            )
            r.raise_for_status()
            data = r.json()
            fy = data.get("fanyi")
            if isinstance(fy, dict):
                tr = (fy.get("tran") or "").strip()
                if tr and _has_zh(tr) and tr.lower() != q.lower():
                    return tr
    except Exception:
        pass
    return None


async def _translate_mymemory(raw: str) -> Optional[str]:
    q = (raw or "").strip()[:450]
    if not q:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=12.0) as client:
            # auto|zh-CN 已不再可靠；海外新闻标题/摘要以英译中为主
            for pair in ("en|zh-CN", "en-GB|zh-CN"):
                r = await client.get(
                    "https://api.mymemory.translated.net/get",
                    params={"q": q, "langpair": pair},
                )
                r.raise_for_status()
                data = r.json()
                out = (
                    (((data or {}).get("responseData") or {}).get("translatedText") or "")
                    .strip()
                    .replace("&#39;", "'")
                )
                if _mymemory_out_valid(q, out):
                    return out
    except Exception:
        pass
    return None


async def _translate_google(raw: str) -> Optional[str]:
    q = (raw or "").strip()[:4500]
    if not q:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": "auto",
                    "tl": "zh-CN",
                    "dt": "t",
                    "q": q,
                },
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                parts: list[str] = []
                for row in data[0]:
                    if isinstance(row, list) and row and isinstance(row[0], str):
                        parts.append(row[0])
                out = "".join(parts).strip()
                if out:
                    return out
    except Exception:
        pass
    return None


async def _translate_openai(raw: str) -> Optional[str]:
    if not settings.openai_api_key:
        return None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None)
        resp = await client.chat.completions.create(
            model=settings.script_model,
            messages=[
                {"role": "system", "content": "请把用户文本翻译成简体中文，只输出译文，不要解释。"},
                {"role": "user", "content": raw[:8000]},
            ],
            temperature=0.1,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or None
    except Exception:
        return None


async def _translate_plain_no_zh_check(raw: str) -> str:
    """已知不含中文的正文：按顺序调用公共接口 / OpenAI。"""
    async with _translate_sem:
        for fn in (_translate_youdao, _translate_mymemory, _translate_google, _translate_openai):
            out = await fn(raw)
            if out:
                return out
    return raw


async def translate_to_zh(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if _has_zh(raw):
        return raw
    # MyMemory 单次 query 不宜过长，分段翻译再拼接
    if len(raw) <= 450:
        return await _translate_plain_no_zh_check(raw)
    parts: list[str] = []
    step = 400
    for i in range(0, len(raw), step):
        chunk = raw[i : i + step].strip()
        if not chunk:
            continue
        if _has_zh(chunk):
            parts.append(chunk)
        else:
            parts.append(await _translate_plain_no_zh_check(chunk))
    return "".join(parts)
