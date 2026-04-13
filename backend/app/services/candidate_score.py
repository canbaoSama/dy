"""轻量候选评分（MVP）；完整方案可接 candidate_scores 表 + 模型排序。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import CandidateTier, NewsItem


def score_news_item(item: NewsItem, source_name: str = "") -> tuple[str, dict]:
    rules: dict[str, float | bool | str] = {}
    score = 0.0

    if item.cleaned_content and len(item.cleaned_content) > 200:
        rules["has_body"] = True
        score += 2.0
    else:
        rules["has_body"] = False

    if item.hero_image_url:
        rules["has_hero"] = True
        score += 1.5
    else:
        rules["has_hero"] = False

    if item.page_screenshot_path:
        rules["has_screenshot"] = True
        score += 1.0
    else:
        rules["has_screenshot"] = False

    wl = ("Reuters", "BBC", "TechCrunch", "Verge")
    src_name = source_name or (item.source.name if item.source else "")
    rules["whitelist_source"] = any(x in src_name for x in wl)
    if rules["whitelist_source"]:
        score += 1.0

    if item.published_at:
        age_h = (datetime.now(timezone.utc) - item.published_at).total_seconds() / 3600
        rules["age_hours"] = round(age_h, 2)
        if age_h < 48:
            score += 1.5
    else:
        rules["age_hours"] = None

    if score >= 4.5:
        tier = CandidateTier.recommended.value
    elif score >= 2.5:
        tier = CandidateTier.neutral.value
    else:
        tier = CandidateTier.not_recommended.value

    rules["total"] = round(score, 2)
    return tier, rules
