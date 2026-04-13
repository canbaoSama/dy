"""句级字幕时间轴：完整方案用 alignment 或 Whisper。"""

from __future__ import annotations

from typing import Any


def build_stub_timeline(full_text: str, duration_sec: float = 35.0) -> list[dict[str, Any]]:
    """均分句级时间轴（占位）。"""
    parts = [p.strip() for p in full_text.replace("。", "。\n").split("\n") if p.strip()]
    if not parts:
        parts = [full_text[:200]]
    n = len(parts)
    step = duration_sec / max(n, 1)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(parts):
        out.append(
            {
                "index": i,
                "start": round(i * step, 2),
                "end": round((i + 1) * step, 2),
                "text": p,
            }
        )
    return out
