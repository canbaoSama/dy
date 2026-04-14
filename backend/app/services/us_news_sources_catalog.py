"""
全球热点 RSS 信源目录（可通过 NEWS_SOURCE_SLUGS 覆盖默认组合）。

策略说明：
- 优先保留“高覆盖聚合 + 权威媒体 + 热度平台”来源；
- 去掉访问量小、议题面窄、重复度高的来源；
- 通过环境变量 NEWS_SOURCE_SLUGS（逗号分隔 slug）启用；留空则使用 DEFAULT_SOURCE_SLUGS。
"""

from __future__ import annotations

from typing import TypedDict


class SourceRow(TypedDict):
    slug: str
    name: str
    name_zh: str
    rss_url: str
    group_zh: str
    note_zh: str


SOURCE_CATALOG: list[SourceRow] = [
    # —— 第一层：聚合热点平台（高覆盖）——
    {
        "slug": "google_news_world",
        "name": "Google News · World (RSS)",
        "name_zh": "谷歌新闻 · 全球",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
        "group_zh": "聚合 / 热点",
        "note_zh": "核心来源之一，全球主流热点。",
    },
    {
        "slug": "google_news_us",
        "name": "Google News · US Headlines (RSS)",
        "name_zh": "谷歌新闻 · 美国头条",
        "rss_url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "group_zh": "聚合 / 热点",
        "note_zh": "美国区头条，补充本地议题。",
    },
    {
        "slug": "yahoo_news_world",
        "name": "Yahoo News · World (RSS)",
        "name_zh": "雅虎新闻 · 全球",
        "rss_url": "https://www.yahoo.com/news/rss/world",
        "group_zh": "聚合 / 热点",
        "note_zh": "英语世界综合热点。",
    },
    {
        "slug": "msn_news_us",
        "name": "Microsoft Start / MSN · US News (RSS)",
        "name_zh": "微软 MSN 新闻 · 美国",
        "rss_url": "https://www.msn.com/en-us/news/rss",
        "group_zh": "聚合 / 热点",
        "note_zh": "MSN 综合热点入口；如失效可替换为频道 RSS。",
    },

    # —— 第二层：权威新闻源（高可信）——
    {
        "slug": "reuters_top",
        "name": "Reuters · Top News",
        "name_zh": "路透社 · 头条",
        "rss_url": "https://feeds.reuters.com/reuters/topNews",
        "group_zh": "权威媒体",
        "note_zh": "国际与财经高价值来源。",
    },
    {
        "slug": "reuters_world",
        "name": "Reuters · World",
        "name_zh": "路透社 · 国际",
        "rss_url": "https://feeds.reuters.com/Reuters/worldNews",
        "group_zh": "权威媒体",
        "note_zh": "",
    },
    {
        "slug": "ap_top",
        "name": "AP News · Top News",
        "name_zh": "美联社 · 头条",
        "rss_url": "https://apnews.com/index.rss",
        "group_zh": "权威媒体",
        "note_zh": "美国和国际突发基础盘。",
    },
    {
        "slug": "bbc_world",
        "name": "BBC · World",
        "name_zh": "BBC · 国际",
        "rss_url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "group_zh": "权威媒体",
        "note_zh": "全球综合要闻。",
    },

    # —— 第三层：热度平台（讨论/搜索热度）——
    {
        "slug": "reddit_worldnews",
        "name": "Reddit · r/worldnews",
        "name_zh": "Reddit · 世界新闻版",
        "rss_url": "https://www.reddit.com/r/worldnews/.rss",
        "group_zh": "热度平台",
        "note_zh": "全球讨论热度补充，偏社区视角。",
    },
    {
        "slug": "google_trends_daily",
        "name": "Google Trends · Daily Trending Searches (US)",
        "name_zh": "谷歌趋势 · 每日热搜（美区）",
        "rss_url": "https://trends.google.com/trending/rss?geo=US",
        "group_zh": "热度平台",
        "note_zh": "用于补充“今天被大量搜索”的爆发话题。",
    },
]

_SLUG_INDEX: dict[str, SourceRow] = {row["slug"]: row for row in SOURCE_CATALOG}


# 未配置 NEWS_SOURCE_SLUGS 时：默认启用目录内全部来源
DEFAULT_SOURCE_SLUGS: tuple[str, ...] = tuple(r["slug"] for r in SOURCE_CATALOG)


def resolve_news_sources(slugs_csv: str | None) -> list[dict[str, str]]:
    """返回 {name, rss_url} 列表，供入库与抓取。"""
    raw = (slugs_csv or "").strip()
    if not raw:
        slugs = list(DEFAULT_SOURCE_SLUGS)
    else:
        slugs = [s.strip() for s in raw.split(",") if s.strip()]

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for slug in slugs:
        row = _SLUG_INDEX.get(slug)
        if not row:
            continue
        url = row["rss_url"]
        if url in seen:
            continue
        seen.add(url)
        out.append({"name": row["name"], "rss_url": url, "slug": slug})
    if not out:
        for slug in DEFAULT_SOURCE_SLUGS:
            row = _SLUG_INDEX[slug]
            out.append({"name": row["name"], "rss_url": row["rss_url"], "slug": slug})
    return out


def catalog_for_api() -> list[dict[str, str]]:
    """供 GET /sources/catalog 返回；含 slug 便于前端勾选。"""
    default_set = set(DEFAULT_SOURCE_SLUGS)
    return [
        {
            "slug": r["slug"],
            "name": r["name"],
            "name_zh": r["name_zh"],
            "rss_url": r["rss_url"],
            "group_zh": r["group_zh"],
            "note_zh": r["note_zh"],
            "in_default": r["slug"] in default_set,
        }
        for r in SOURCE_CATALOG
    ]
