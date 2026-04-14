"""Remotion / FFmpeg 渲染占位：完整方案调用 remotion CLI 或 worker-render。"""

from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path
from urllib.parse import quote_plus
from urllib.parse import urljoin

import httpx

def _escape_drawtext(text: str) -> str:
    s = (text or "").strip().replace("\n", " ")
    s = s.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%")
    return s


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode('utf-8', errors='ignore')[:500]}")


def _build_segments(script: dict) -> list[str]:
    lines: list[str] = []
    hook = str(script.get("hook") or "").strip()
    if hook:
        lines.append(hook)
    for b in (script.get("body") or [])[:3]:
        t = str(b).strip()
        if t:
            lines.append(t)
    ending = str(script.get("ending") or "").strip()
    if ending:
        lines.append(ending)
    if not lines:
        lines = ["海外新闻快讯", "正在生成内容", "请稍后查看"]
    return lines[:5]


_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


def _pick_background_image(job_dir: Path, meta: dict) -> Path | None:
    for s in list(meta.get("user_image_paths") or []):
        p = Path(str(s))
        if p.exists():
            return p
    local_hero = job_dir.parent.parent / "assets" / job_dir.name / "hero.jpg"
    if local_hero.exists():
        return local_hero
    screenshot = str(meta.get("page_screenshot_path") or "").strip()
    if screenshot:
        p = Path(screenshot)
        if p.exists():
            return p
    return None


async def _collect_extra_images(job_dir: Path, meta: dict, limit: int = 3) -> list[Path]:
    article_url = str(meta.get("article_url") or "").strip()
    if not article_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
            r = await client.get(
                article_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            r.raise_for_status()
            html = r.text
    except Exception:
        return []

    urls: list[str] = []
    for m in _IMG_SRC_RE.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        full = urljoin(article_url, raw)
        low = full.lower()
        if not low.startswith(("http://", "https://")):
            continue
        if any(low.endswith(ext) for ext in (".svg", ".gif")):
            continue
        if full in urls:
            continue
        urls.append(full)
        if len(urls) >= limit:
            break

    out: list[Path] = []
    for i, u in enumerate(urls):
        p = job_dir / f"article_img_{i}.jpg"
        try:
            async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
                rr = await client.get(
                    u,
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                        "Referer": article_url,
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    },
                )
                rr.raise_for_status()
                p.write_bytes(rr.content)
                out.append(p)
        except Exception:
            continue
    return out


def _extract_keywords(meta: dict, limit: int = 4) -> list[str]:
    raw = " ".join(
        [
            str(meta.get("title") or ""),
            str(meta.get("summary") or ""),
            str(meta.get("source") or ""),
        ]
    ).lower()
    words = re.findall(r"[a-z]{4,}", raw)
    stop = {
        "news",
        "says",
        "said",
        "report",
        "reports",
        "politics",
        "media",
        "video",
        "latest",
        "about",
        "with",
        "from",
        "that",
        "this",
    }
    uniq: list[str] = []
    for w in words:
        if w in stop or w in uniq:
            continue
        uniq.append(w)
        if len(uniq) >= limit:
            break
    if not uniq:
        uniq = ["world", "news", "breaking"]
    return uniq


async def _collect_web_images(job_dir: Path, meta: dict, limit: int = 3) -> list[Path]:
    keywords = _extract_keywords(meta, limit=4)
    if not keywords:
        return []
    queries = [" ".join(keywords)]
    queries.extend(keywords[:3])
    pages_list: list[dict] = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for q in queries:
            api = (
                "https://commons.wikimedia.org/w/api.php"
                f"?action=query&generator=search&gsrsearch={quote_plus(q)}"
                "&gsrnamespace=6&gsrlimit=8&prop=imageinfo&iiprop=url|mime&format=json"
            )
            try:
                r = await client.get(api, headers={"User-Agent": "NewsFactory/0.1"})
                r.raise_for_status()
                data = r.json()
                pages = (data.get("query") or {}).get("pages") or {}
                pages_list.extend(list(pages.values()))
            except Exception:
                continue

    urls: list[str] = []
    for pg in pages_list:
        infos = pg.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0] or {}
        url = str(info.get("url") or "").strip()
        mime = str(info.get("mime") or "").lower()
        low = url.lower()
        if not url.startswith(("http://", "https://")):
            continue
        if mime and not mime.startswith("image/"):
            continue
        if any(low.endswith(ext) for ext in (".svg", ".gif", ".webp")):
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break

    out: list[Path] = []
    for i, u in enumerate(urls):
        p = job_dir / f"web_img_{i}.jpg"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                rr = await client.get(
                    u,
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                        "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
                    },
                )
                rr.raise_for_status()
                p.write_bytes(rr.content)
                out.append(p)
        except Exception:
            continue
    return out


async def _collect_web_video_clips(job_dir: Path, meta: dict, limit: int = 2, duration_sec: float = 4.0) -> list[Path]:
    """按新闻关键词从 Wikimedia 拉取相关视频素材并转为竖屏片段。"""
    keywords = _extract_keywords(meta, limit=4)
    if not keywords:
        return []
    queries = [" ".join(keywords)]
    queries.extend(keywords[:3])
    pages_list: list[dict] = []
    async with httpx.AsyncClient(timeout=24.0, follow_redirects=True) as client:
        for q in queries:
            api = (
                "https://commons.wikimedia.org/w/api.php"
                f"?action=query&generator=search&gsrsearch={quote_plus(q)}"
                "&gsrnamespace=6&gsrlimit=10&prop=imageinfo&iiprop=url|mime&format=json"
            )
            try:
                r = await client.get(api, headers={"User-Agent": "NewsFactory/0.1"})
                r.raise_for_status()
                data = r.json()
                pages = (data.get("query") or {}).get("pages") or {}
                pages_list.extend(list(pages.values()))
            except Exception:
                continue

    urls: list[str] = []
    for pg in pages_list:
        infos = pg.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0] or {}
        url = str(info.get("url") or "").strip()
        mime = str(info.get("mime") or "").lower()
        if not url.startswith(("http://", "https://")):
            continue
        if not mime.startswith("video/"):
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break

    out: list[Path] = []
    for i, u in enumerate(urls):
        p = job_dir / f"web_clip_{i}.mp4"
        try:
            await _run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "0",
                    "-i",
                    u,
                    "-t",
                    f"{max(2.0, min(duration_sec, 6.0)):.2f}",
                    "-an",
                    "-vf",
                    "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p",
                    "-r",
                    "30",
                    str(p),
                ]
            )
            if p.exists():
                out.append(p)
        except Exception:
            continue
    return out


def _split_subtitles(meta: dict) -> list[str]:
    narration_text = str(meta.get("narration_text") or "").strip()
    if narration_text:
        lines = [x.strip() for x in narration_text.splitlines() if x.strip()]
        if lines:
            tone = str(meta.get("subtitle_tone") or "").strip().lower()
            if tone in {"精简", "brief", "short"}:
                return [ln[:26] for ln in lines[:4]]
            return lines[:4]
    return _build_segments(meta.get("script") or {})[:4]


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round((sec - math.floor(sec)) * 1000))
    total = int(sec)
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_subtitle_draws(lines: list[str], total_duration: float, font_path: str) -> list[str]:
    if not lines:
        return []
    slot = total_duration / len(lines)
    filters: list[str] = []
    for i, line in enumerate(lines):
        start = i * slot
        end = min(total_duration, (i + 1) * slot)
        text = _escape_drawtext(line[:46])
        filters.append(
            "drawtext="
            f"fontfile={font_path}:"
            f"text='{text}':"
            "x=(w-text_w)/2:y=h-120:"
            "fontsize=38:fontcolor=white:borderw=2:bordercolor=black@0.88:"
            f"enable='between(t,{start:.2f},{end:.2f})'"
        )
    return filters


async def render_video_stub(job_dir: Path, meta: dict) -> tuple[str | None, str | None]:
    """MVP: 生成可播放竖屏视频（优先真实图片 + 时间轴字幕）。"""
    job_dir.mkdir(parents=True, exist_ok=True)
    video_path = job_dir / "video.mp4"
    preview_path = job_dir / "preview.jpg"
    temp_video = job_dir / "video_noaudio.mp4"
    subtitle_path = job_dir / "subtitles.srt"
    duration = float(meta.get("duration_sec") or 35.0)
    duration = max(10.0, min(duration, 90.0))
    source = _escape_drawtext(str(meta.get("source") or "NEWS FACTORY"))
    subtitle_lines = _split_subtitles(meta)
    font = "/usr/share/fonts/truetype/arphic/uming.ttc"

    must_uploaded = bool(meta.get("must_use_uploaded_assets", False))
    prefer_video = bool(meta.get("prefer_video_assets", False))

    bg_images: list[Path] = []
    for s in list(meta.get("user_image_paths") or []):
        p = Path(str(s))
        if p.exists():
            bg_images.append(p)
    if not must_uploaded:
        bg_image = _pick_background_image(job_dir, meta)
        if bg_image is not None and bg_image.exists():
            bg_images.append(bg_image)
        if not bg_images:
            remote = str(meta.get("hero_image_url") or "").strip()
            if remote:
                try:
                    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
                        r = await client.get(
                            remote,
                            headers={
                                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                                "Referer": str(meta.get("article_url") or remote),
                                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            },
                        )
                        r.raise_for_status()
                        remote_hero = job_dir / "remote_hero.jpg"
                        remote_hero.write_bytes(r.content)
                        bg_images.append(remote_hero)
                except Exception:
                    pass
        extra_images = await _collect_extra_images(job_dir, meta, limit=3)
        for p in extra_images:
            if p.exists():
                bg_images.append(p)
        web_images = await _collect_web_images(job_dir, meta, limit=3)
        for p in web_images:
            if p.exists():
                bg_images.append(p)
    dedup: list[Path] = []
    seen: set[str] = set()
    for p in bg_images:
        k = str(p.resolve()) if p.exists() else str(p)
        if k not in seen:
            seen.add(k)
            dedup.append(p)
    bg_images = dedup[:4]
    if len(bg_images) > 4:
        bg_images = bg_images[:4]
    web_clips: list[Path] = []
    for s in list(meta.get("user_video_paths") or []):
        p = Path(str(s))
        if p.exists():
            web_clips.append(p)
    if not must_uploaded:
        extra_clips = await _collect_web_video_clips(job_dir, meta, limit=2, duration_sec=min(5.0, duration / 3.0))
        for p in extra_clips:
            if p.exists():
                web_clips.append(p)
    clip_dedup: list[Path] = []
    clip_seen: set[str] = set()
    for p in web_clips:
        k = str(p.resolve())
        if k not in clip_seen:
            clip_seen.add(k)
            clip_dedup.append(p)
    web_clips = clip_dedup[:3]
    if not bg_images and not web_clips:
        raise RuntimeError("未获取到相关新闻素材，已停止渲染（禁用画布兜底）")

    srt_lines: list[str] = []
    step = duration / max(len(subtitle_lines), 1)
    for i, line in enumerate(subtitle_lines, start=1):
        st = _fmt_ts((i - 1) * step)
        ed = _fmt_ts(min(duration, i * step))
        srt_lines.append(f"{i}\n{st} --> {ed}\n{line}\n")
    subtitle_path.write_text("\n".join(srt_lines), encoding="utf-8")

    seg_dur = duration / max(len(subtitle_lines), 1)
    part_files: list[Path] = []
    for i, line in enumerate(subtitle_lines):
        part = job_dir / f"part_{i}.mp4"
        part_files.append(part)
        subtitle_text = _escape_drawtext(line[:46])
        head_overlay = (
            "drawbox=x=0:y=0:w=iw:h=110:color=black@0.35:t=fill,"
            f"drawtext=fontfile={font}:text='{source}':x=24:y=30:fontsize=30:fontcolor=white:borderw=1:bordercolor=black@0.7,"
            "drawbox=x=0:y=h-190:w=iw:h=190:color=black@0.42:t=fill,"
            f"drawtext=fontfile={font}:text='{subtitle_text}':x=(w-text_w)/2:y=h-118:fontsize=38:fontcolor=white:borderw=2:bordercolor=black@0.88,"
            "format=yuv420p"
        )

        if bg_images:
            img = bg_images[i % len(bg_images)]
            vf = (
                "scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280,"
                "eq=brightness=-0.05:saturation=1.12,"
                f"{head_overlay}"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-t",
                f"{seg_dur:.2f}",
                "-i",
                str(img),
                "-vf",
                vf,
                "-r",
                "30",
                "-pix_fmt",
                "yuv420p",
                str(part),
            ]
            if web_clips and (prefer_video or i in {1, 3}):
                clip = web_clips[(i - 1) % len(web_clips)]
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(clip),
                    "-t",
                    f"{seg_dur:.2f}",
                    "-vf",
                    f"eq=brightness=-0.03:saturation=1.08,{head_overlay}",
                    "-r",
                    "30",
                    "-pix_fmt",
                    "yuv420p",
                    str(part),
                ]
            await _run_ffmpeg(cmd)
        else:
            clip = web_clips[i % len(web_clips)]
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(clip),
                "-t",
                f"{seg_dur:.2f}",
                "-vf",
                f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=brightness=-0.03:saturation=1.08,{head_overlay}",
                "-r",
                "30",
                "-pix_fmt",
                "yuv420p",
                str(part),
            ]
            await _run_ffmpeg(cmd)

    concat_list = job_dir / "parts.txt"
    concat_list.write_text("".join(f"file '{p.name}'\n" for p in part_files), encoding="utf-8")
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(temp_video),
        ]
    )

    audio_path = Path(str(meta.get("audio_path") or "")).expanduser()
    has_real_audio = audio_path.exists() and audio_path.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".ogg"}
    if has_real_audio:
        await _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(temp_video),
                "-i",
                str(audio_path),
                "-t",
                f"{duration:.2f}",
                "-af",
                "apad",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ]
        )
    else:
        await _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(temp_video),
                "-f",
                "lavfi",
                "-t",
                f"{duration:.2f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-shortest",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ]
        )

    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-ss",
            "00:00:01",
            "-vframes",
            "1",
            str(preview_path),
        ]
    )
    return str(video_path), str(preview_path if preview_path.exists() else "")
