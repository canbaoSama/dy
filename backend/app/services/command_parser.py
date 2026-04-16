"""聊天式命令解析（MVP）：与文档 6.9 最少命令对齐，可换 QClaw 调度。"""

from __future__ import annotations

import re
from typing import Any, Literal

ParsedKind = Literal[
    "candidates",
    "make_job",
    "asset_candidates",
    "select_assets",
    "step_subtitle_draft",
    "step_script",
    "step_timeline",
    "step_audio",
    "step_render",
    "confirm_subtitles",
    "confirm_audio",
    "rewrite_script",
    "render",
    "titles",
    "intro",
    "unknown",
]


def parse_command(text: str) -> dict[str, Any]:
    t = text.strip()
    if not t:
        return {"kind": "unknown", "raw": text}

    m = re.search(r"做\s*第\s*(\d+)\s*条", t)
    if m:
        return {"kind": "make_job", "index": int(m.group(1)), "raw": text}

    if re.search(r"素材候选|候选素材|素材列表|给我素材", t, re.I):
        return {"kind": "asset_candidates", "raw": text}
    m = re.search(r"选素材\s*([0-9,\s，]+)", t, re.I)
    if m:
        raw = m.group(1)
        nums = [int(x) for x in re.findall(r"\d+", raw)]
        return {"kind": "select_assets", "indices": nums, "raw": text}

    if re.search(r"生成字幕|字幕草稿|字幕步骤|步骤1", t, re.I):
        return {"kind": "step_subtitle_draft", "raw": text}
    if re.search(r"生成脚本|脚本步骤|步骤2", t, re.I):
        return {"kind": "step_script", "raw": text}
    if re.search(r"字幕时间轴|时间轴|步骤3", t, re.I):
        return {"kind": "step_timeline", "raw": text}
    if re.search(r"确认字幕|字幕确认|继续音频", t, re.I):
        return {"kind": "confirm_subtitles", "raw": text}
    if re.search(r"生成音频|音频步骤|步骤4", t, re.I):
        return {"kind": "step_audio", "raw": text}
    if re.search(r"确认音频|音频确认|继续合成", t, re.I):
        return {"kind": "confirm_audio", "raw": text}
    if re.search(r"合成视频|渲染视频|步骤4", t, re.I):
        return {"kind": "step_render", "raw": text}

    if re.search(r"(今天|今日)?\s*候选|给我.*新闻|候选列表", t, re.I):
        return {"kind": "candidates", "raw": text}

    m = re.search(r"开始渲染|渲染\s*(\d+)?", t)
    if m:
        jid = m.group(1)
        return {"kind": "render", "job_id": int(jid) if jid else None, "raw": text}

    if re.search(r"更炸|更口语|开头|新闻联播|普通人", t):
        return {"kind": "rewrite_script", "instruction": t, "raw": text}

    if re.search(r"给我标题|标题建议", t):
        return {"kind": "titles", "raw": text}

    if re.search(r"给我简介|简介建议", t):
        return {"kind": "intro", "raw": text}

    return {"kind": "unknown", "raw": text}
