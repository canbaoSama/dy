"""
美国时政 / 特朗普相关 RSS 信源目录。

说明（必读）：
- X（推特）无官方稳定 RSS；特朗普常用账号 @realDonaldTrump 需 RSSHub 自建或第三方实例，公共实例易限流。
- 下列 URL 为业界常见公开订阅地址；若站点改版导致 404，请自行替换为官网「RSS」页最新链接。
- 通过环境变量 NEWS_SOURCE_SLUGS（逗号分隔 slug）启用；留空则使用 DEFAULT_SOURCE_SLUGS。
"""

from __future__ import annotations

from typing import TypedDict


class SourceRow(TypedDict):
    slug: str
    name: str
    rss_url: str
    group_zh: str
    note_zh: str


SOURCE_CATALOG: list[SourceRow] = [
    # —— 特朗普 / 个人发声（RSS 能力有限）——
    {
        "slug": "trump_campaign_site",
        "name": "Donald J. Trump · 官网摘要 (WordPress Feed)",
        "rss_url": "https://www.donaldjtrump.com/feed/",
        "group_zh": "特朗普 / 个人与竞选",
        "note_zh": "竞选官网常见 /feed/；若改版请以站点为准。",
    },
    {
        "slug": "trump_x_rsshub_public",
        "name": "X @realDonaldTrump · RSSHub 公共实例",
        "rss_url": "https://rsshub.app/twitter/user/realDonaldTrump",
        "group_zh": "特朗普 / 个人与竞选",
        "note_zh": "非官方；公共 rsshub.app 常限流/不可用，强烈建议自建 RSSHub 后把域名写进自定义条目。",
    },
    {
        "slug": "whitehouse_news",
        "name": "The White House · News",
        "rss_url": "https://www.whitehouse.gov/news/feed/",
        "group_zh": "美国联邦政府",
        "note_zh": "白宫新闻发布；时政背景稿源。",
    },
    # —— 通讯社 / 一线大媒体 ——
    {
        "slug": "reuters_top",
        "name": "Reuters · Top News",
        "rss_url": "https://feeds.reuters.com/reuters/topNews",
        "group_zh": "通讯社 / 综合",
        "note_zh": "",
    },
    {
        "slug": "reuters_world",
        "name": "Reuters · World",
        "rss_url": "https://feeds.reuters.com/Reuters/worldNews",
        "group_zh": "通讯社 / 综合",
        "note_zh": "",
    },
    {
        "slug": "reuters_politics",
        "name": "Reuters · Politics",
        "rss_url": "https://feeds.reuters.com/reuters/politicsNews",
        "group_zh": "通讯社 / 综合",
        "note_zh": "",
    },
    {
        "slug": "reuters_us",
        "name": "Reuters · US",
        "rss_url": "https://feeds.reuters.com/reuters/USdomesticNews",
        "group_zh": "通讯社 / 综合",
        "note_zh": "",
    },
    {
        "slug": "ap_top",
        "name": "AP News · Top News (index)",
        "rss_url": "https://apnews.com/index.rss",
        "group_zh": "通讯社 / 综合",
        "note_zh": "若 404，请到 apnews.com 页脚或「RSS」页复制最新地址替换 slug ap_top。",
    },
    {
        "slug": "npr_national",
        "name": "NPR · National",
        "rss_url": "https://feeds.npr.org/1003/rss.rss",
        "group_zh": "通讯社 / 综合",
        "note_zh": "",
    },
    {
        "slug": "npr_politics",
        "name": "NPR · Politics",
        "rss_url": "https://feeds.npr.org/1014/rss.rss",
        "group_zh": "通讯社 / 综合",
        "note_zh": "",
    },
    {
        "slug": "bbc_us_canada",
        "name": "BBC · US & Canada",
        "rss_url": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
        "group_zh": "通讯社 / 综合",
        "note_zh": "英媒视角下的美国新闻。",
    },
    # —— 美国主流新闻站 ——
    {
        "slug": "cnn_top",
        "name": "CNN · Top Stories",
        "rss_url": "http://rss.cnn.com/rss/cnn_topstories.rss",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "cnn_politics",
        "name": "CNN · Politics",
        "rss_url": "http://rss.cnn.com/rss/cnn_allpolitics.rss",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "fox_latest",
        "name": "Fox News · Latest",
        "rss_url": "http://feeds.foxnews.com/foxnews/latest",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "fox_politics",
        "name": "Fox News · Politics",
        "rss_url": "http://feeds.foxnews.com/foxnews/politics",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "nbc_politics",
        "name": "NBC News · Politics",
        "rss_url": "https://feeds.nbcnews.com/nbcnews/public/politics",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "cbs_politics",
        "name": "CBS News · Politics",
        "rss_url": "https://www.cbsnews.com/latest/rss/politics",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "abc_politics",
        "name": "ABC News · Politics",
        "rss_url": "http://abcnews.go.com/abcnews/politicsheadlines",
        "group_zh": "美国主流网站",
        "note_zh": "长期使用的 politics 头条 RSS；若格式变化请换官方链接。",
    },
    {
        "slug": "politico_picks",
        "name": "Politico · Politico Picks",
        "rss_url": "https://www.politico.com/rss/politicopicks.xml",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "politico_politics08",
        "name": "Politico · Politics",
        "rss_url": "https://www.politico.com/rss/politics08.xml",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "thehill_home",
        "name": "The Hill · Home News",
        "rss_url": "https://thehill.com/homenews/feed/",
        "group_zh": "美国主流网站",
        "note_zh": "国会山报，偏国会与政局。",
    },
    {
        "slug": "axios_top",
        "name": "Axios · Top",
        "rss_url": "https://api.axios.com/feed/",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "nyt_politics",
        "name": "NYTimes · Politics",
        "rss_url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "nyt_us",
        "name": "NYTimes · US",
        "rss_url": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "wapo_politics",
        "name": "Washington Post · Politics",
        "rss_url": "https://feeds.washingtonpost.com/rss/politics",
        "group_zh": "美国主流网站",
        "note_zh": "部分区域可能需订阅；拉不到再换 slug。",
    },
    {
        "slug": "wsj_opinion",
        "name": "WSJ · Opinion",
        "rss_url": "https://feeds.a.dj.com/rss/RSSOpinion.xml",
        "group_zh": "美国主流网站",
        "note_zh": "道琼斯 feeds 域名；偏评论。",
    },
    {
        "slug": "usatoday_news",
        "name": "USA Today · News",
        "rss_url": "https://rssfeeds.usatoday.com/usatoday-NewsTopStories",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "nypost_us",
        "name": "New York Post · US News",
        "rss_url": "https://nypost.com/us-news/feed/",
        "group_zh": "美国主流网站",
        "note_zh": "",
    },
    {
        "slug": "breitbart",
        "name": "Breitbart · News",
        "rss_url": "https://feeds.feedburner.com/breitbart",
        "group_zh": "美国主流网站",
        "note_zh": "立场鲜明，仅作选题来源之一。",
    },
    {
        "slug": "the_guardian_us",
        "name": "The Guardian · US",
        "rss_url": "https://www.theguardian.com/us-news/rss",
        "group_zh": "美国主流网站",
        "note_zh": "英媒美国版，可作交叉视角。",
    },
    {
        "slug": "nbc_top",
        "name": "NBC News · Top Stories",
        "rss_url": "https://feeds.nbcnews.com/nbcnews/public/world",
        "group_zh": "美国主流网站",
        "note_zh": "若需纯美国可把 slug 换为 nbc_politics。",
    },
]

_SLUG_INDEX: dict[str, SourceRow] = {row["slug"]: row for row in SOURCE_CATALOG}


# 未配置 NEWS_SOURCE_SLUGS 时：美国时政 + 通讯社为主（不含易失效的 RSSHub 公共实例）
DEFAULT_SOURCE_SLUGS: tuple[str, ...] = (
    "reuters_politics",
    "reuters_top",
    "npr_politics",
    "cnn_top",
    "politico_picks",
    "thehill_home",
    "fox_politics",
)


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
            "rss_url": r["rss_url"],
            "group_zh": r["group_zh"],
            "note_zh": r["note_zh"],
            "in_default": r["slug"] in default_set,
        }
        for r in SOURCE_CATALOG
    ]
