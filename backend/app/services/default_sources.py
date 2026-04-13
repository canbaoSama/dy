"""信源目录已迁至 us_news_sources_catalog；本模块保留兼容导出。"""

from app.services.us_news_sources_catalog import (
    DEFAULT_SOURCE_SLUGS,
    SOURCE_CATALOG,
    catalog_for_api,
    resolve_news_sources,
)

__all__ = [
    "DEFAULT_SOURCE_SLUGS",
    "SOURCE_CATALOG",
    "catalog_for_api",
    "resolve_news_sources",
]
