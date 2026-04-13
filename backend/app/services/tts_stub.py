"""TTS 模块占位：完整方案接入 Azure/MiniMax/ElevenLabs 等。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import settings


async def _run_cmd(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    msg = (out or b"").decode("utf-8", errors="ignore") + (err or b"").decode("utf-8", errors="ignore")
    return proc.returncode, msg


async def _probe_duration(path: Path) -> float:
    code, out = await _run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if code != 0:
        return 0.0
    try:
        return max(0.0, float(out.strip().splitlines()[0]))
    except Exception:
        return 0.0


async def synthesize_narration(text: str, job_dir: Path, target_duration: float | None = None) -> tuple[str, float]:
    """返回 (本地文件路径, 时长秒)。优先生成真实音频，失败再降级。"""
    job_dir.mkdir(parents=True, exist_ok=True)
    txt_path = job_dir / "narration_text.txt"
    txt_path.write_text((text or "").strip()[:3000] or "news update", encoding="utf-8")
    wav_path = job_dir / "narration.wav"
    mp3_path = job_dir / "narration.mp3"

    # 方案A：ffmpeg flite（离线，质量一般，但可直接出声）
    code, _ = await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"flite=textfile={txt_path.as_posix()}:voice=slt",
            str(wav_path),
        ]
    )
    if code == 0 and wav_path.exists():
        dur = await _probe_duration(wav_path)
        if dur > 0:
            return str(wav_path), dur

    # 方案B：降级为占位文本（最终渲染层会补静音轨）
    placeholder = job_dir / "narration_placeholder.txt"
    placeholder.write_text("[TTS 占位]\n\n" + text[:2000], encoding="utf-8")
    if target_duration is None:
        duration = 18.0
    else:
        duration = max(10.0, min(float(target_duration), 22.0))
    _ = mp3_path  # keep path reserved for future providers
    return str(placeholder), duration
