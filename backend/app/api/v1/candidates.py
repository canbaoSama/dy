from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.candidate_list import query_candidate_rows, serialize_candidates

router = APIRouter(tags=["candidates"])


@router.get("/candidates")
async def list_candidates(session: AsyncSession = Depends(get_db)):
    rows = await query_candidate_rows(session)
    return {"items": await serialize_candidates(rows)}
