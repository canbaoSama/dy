from __future__ import annotations

import re

from app.config import settings

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")


def _has_zh(text: str) -> bool:
    return bool(_ZH_RE.search(text))


async def translate_to_zh(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if _has_zh(raw):
        return raw
    if not settings.openai_api_key:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx",
                        "sl": "auto",
                        "tl": "zh-CN",
                        "dt": "t",
                        "q": raw,
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
        # 备用公共翻译端点（有时比 gtx 更稳定）
        try:
            import httpx

            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(
                    "https://api.mymemory.translated.net/get",
                    params={"q": raw, "langpair": "auto|zh-CN"},
                )
                r.raise_for_status()
                data = r.json()
                out = (
                    (((data or {}).get("responseData") or {}).get("translatedText") or "")
                    .strip()
                    .replace("&#39;", "'")
                )
                if out:
                    return out
        except Exception:
            pass
        return raw
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None)
        resp = await client.chat.completions.create(
            model=settings.script_model,
            messages=[
                {"role": "system", "content": "请把用户文本翻译成简体中文，只输出译文，不要解释。"},
                {"role": "user", "content": raw},
            ],
            temperature=0.1,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or raw
    except Exception:
        return raw
