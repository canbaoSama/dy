"""TTS 模块占位：完整方案接入 Azure/MiniMax/ElevenLabs 等。"""

from __future__ import annotations

import asyncio
import re
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


async def synthesize_narration(
    text: str,
    job_dir: Path,
    target_duration: float | None = None,
    voice: str | None = None,
) -> tuple[str, float]:
    """返回 (本地文件路径, 时长秒)。优先生成真实音频，失败再降级。"""
    job_dir.mkdir(parents=True, exist_ok=True)
    txt_path = job_dir / "narration_text.txt"
    txt_path.write_text((text or "").strip()[:3000] or "news update", encoding="utf-8")
    wav_path = job_dir / "narration.wav"
    mp3_path = job_dir / "narration.mp3"
    normalized_wav = job_dir / "narration_norm.wav"

    clean_text = (text or "").strip()[:3000] or "news update"
    has_zh = bool(re.search(r"[\u4e00-\u9fff]", clean_text))

    # 方案A：edge-tts（中文神经音色）
    try:
        import edge_tts  # type: ignore

        selected_voice = (voice or "").strip()
        if selected_voice and not re.fullmatch(r"[A-Za-z0-9-]+", selected_voice):
            selected_voice = ""
        if not selected_voice:
            selected_voice = "zh-CN-XiaoxiaoNeural" if has_zh else "en-US-JennyNeural"
        communicate = edge_tts.Communicate(clean_text, voice=selected_voice, rate="-5%")
        await communicate.save(str(mp3_path))
        code_n, _ = await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(mp3_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(normalized_wav),
            ]
        )
        if code_n == 0 and normalized_wav.exists():
            dur = await _probe_duration(normalized_wav)
            if dur > 0:
                return str(normalized_wav), dur
    except Exception:
        pass

    # 方案A：ffmpeg flite（离线，质量一般，但可直接出声）
    if not has_zh:
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


async def synthesize_preview_mp3(voice: str, text: str, out_path: Path) -> str:
    """生成试听 mp3，返回本地路径。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    selected_voice = (voice or "").strip()
    if not selected_voice or not re.fullmatch(r"[A-Za-z0-9-]+", selected_voice):
        raise ValueError("非法 voice")
    sample_text = (text or "").strip()[:240]
    if not sample_text:
        sample_text = "你好，这是一段音色试听。"
    import edge_tts  # type: ignore

    communicate = edge_tts.Communicate(sample_text, voice=selected_voice, rate="-5%")
    await communicate.save(str(out_path))
    return str(out_path)
