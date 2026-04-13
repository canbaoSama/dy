from fastapi import APIRouter

from app.services.us_news_sources_catalog import catalog_for_api

router = APIRouter(tags=["sources"])


@router.get("/sources/catalog")
async def sources_catalog():
    """列出可选 RSS 信源（slug / 分组 / 说明），供运营勾选后写入 NEWS_SOURCE_SLUGS。"""
    return {"items": catalog_for_api()}
