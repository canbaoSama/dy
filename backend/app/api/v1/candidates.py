import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.candidate_list import query_candidate_rows, serialize_candidates
from app.services.candidate_translate import translate_to_zh

router = APIRouter(tags=["candidates"])


@router.get("/candidates")
async def list_candidates(session: AsyncSession = Depends(get_db)):
    rows = await query_candidate_rows(session)
    return {"items": await serialize_candidates(rows)}


class CandidateTranslateItemIn(BaseModel):
    index: int
    title: str
    summary: str | None = None
    source: str | None = None
    url: str


class CandidateTranslateRequest(BaseModel):
    items: list[CandidateTranslateItemIn]


@router.post("/candidates/translate")
async def translate_candidates(payload: CandidateTranslateRequest):
    async def _safe_translate(text: str | None) -> str | None:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            return await translate_to_zh(raw)
        except Exception:
            return raw

    out: list[dict] = []
    for it in payload.items:
        title_zh, summary_zh = await asyncio.gather(
            _safe_translate(it.title),
            _safe_translate(it.summary),
        )
        out.append(
            {
                "index": it.index,
                "url": it.url,
                "title_zh": title_zh or it.title,
                "summary_zh": summary_zh or (it.summary or ""),
            }
        )
    return {"items": out}
