"""聊天式命令解析（MVP）：与文档 6.9 最少命令对齐，可换 QClaw 调度。"""

from __future__ import annotations

import re
from typing import Any, Literal

ParsedKind = Literal[
    "candidates",
    "make_job",
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

    if re.search(r"(今天|今日)?\s*候选|给我.*新闻|候选列表", t, re.I):
        return {"kind": "candidates", "raw": text}

    m = re.search(r"做\s*第\s*(\d+)\s*条", t)
    if m:
        return {"kind": "make_job", "index": int(m.group(1)), "raw": text}

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
