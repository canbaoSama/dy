from __future__ import annotations

import asyncio
import re
from typing import Optional

from app.config import settings

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")
# 有道 jsonapi_s：「number + 序号」易误走词典；半角数字整句也不稳定 → 归一成「# 序号」+ 无 URL 时全角数字
_NUM_WORD_DIGIT = re.compile(r"(?i)\bnumber\s+(\d+)")
_NO_ABBREV_DIGIT = re.compile(r"(?i)\bno\.\s*(\d+)")

# 并发翻译时限制出站连接；数值过大时有道等接口容易偶发空结果/英文回退
_translate_sem = asyncio.Semaphore(5)


def _ascii_digits_to_fullwidth(s: str) -> str:
    return "".join(chr(ord(c) - ord("0") + 0xFF10) if "0" <= c <= "9" else c for c in s)


def _normalize_for_third_party_translate(s: str) -> str:
    """
    将用于调用公共翻译接口的文本做轻量归一化：
    - 「number 25 / No.5」→「# 25」类写法，避免有道把 number 当词典关键词；
    - 无 URL 时再将剩余半角数字改为全角，进一步降低整句 fanyi 失败率；
    - 含 URL 时不做全角化，避免破坏链接；上述 # 替换仍尽量只影响英文标题句式。
    """
    t = (s or "").strip()
    if not t:
        return t
    t = _NUM_WORD_DIGIT.sub(lambda m: "# " + m.group(1), t)
    t = _NO_ABBREV_DIGIT.sub(lambda m: "# " + m.group(1), t)
    if "://" not in t:
        return _ascii_digits_to_fullwidth(t)
    return t


def _has_zh(text: str) -> bool:
    return bool(_ZH_RE.search(text))


def _mymemory_out_valid(q: str, out: str) -> bool:
    if not out or not out.strip():
        return False
    u = out.upper()
    if "INVALID SOURCE LANGUAGE" in u or "QUERY LENGTH LIMIT" in u:
        return False
    if "MYMEMORY WARNING" in u or "QUERY TOO LONG" in u or "USAGE LIMITS" in u:
        return False
    if out.strip().lower() == q.strip().lower():
        return False
    # 英译中：合法结果应含中文，避免把额度提示等英文当作译文
    if not _has_zh(out):
        return False
    return True


def _looks_like_valid_zh_translation(src: str, out: str) -> bool:
    """公共接口偶发返回原文/非中文；用于过滤后再尝试下一供应商。"""
    o = (out or "").strip()
    if not o:
        return False
    if o.casefold() == (src or "").strip().casefold():
        return False
    return _has_zh(o)


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
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://fanyi.youdao.com/",
                },
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
                if out and _looks_like_valid_zh_translation(q, out):
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
        if out and _looks_like_valid_zh_translation(raw, out):
            return out
        return None
    except Exception:
        return None


async def _translate_plain_no_zh_check(raw: str) -> str:
    """已知不含中文的正文：按顺序调用公共接口 / OpenAI。"""
    async with _translate_sem:
        for fn in (_translate_youdao, _translate_mymemory, _translate_google, _translate_openai):
            out = await fn(raw)
            if out and _looks_like_valid_zh_translation(raw, out):
                return out
    return raw


async def translate_to_zh(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if _has_zh(raw):
        return raw

    work = _normalize_for_third_party_translate(raw)

    async def _short_once() -> str:
        return await _translate_plain_no_zh_check(work)

    async def _chunked_once() -> str:
        parts: list[str] = []
        step = 400
        for i in range(0, len(work), step):
            chunk = work[i : i + step].strip()
            if not chunk:
                continue
            if _has_zh(chunk):
                parts.append(chunk)
            else:
                parts.append(await _translate_plain_no_zh_check(chunk))
        return "".join(parts)

    # MyMemory 单次 query 不宜过长，分段翻译再拼接
    out = ""
    for attempt in range(3):
        if len(work) <= 450:
            out = await _short_once()
        else:
            out = await _chunked_once()
        if _has_zh(out):
            return out
        if attempt < 2:
            await asyncio.sleep(0.2 + 0.2 * attempt)
    return out
