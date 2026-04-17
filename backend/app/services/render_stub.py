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
    s = (text or "").strip()
    # drawtext 多行需使用 \n，不能压成单行，否则长句会左右超框被裁。
    s = s.replace("\n", r"\n")
    s = s.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%")
    return s


def _wrap_caption_text(text: str, max_chars_per_line: int) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    if max_chars_per_line <= 0:
        return t
    out: list[str] = []
    cur = ""
    for ch in t:
        cur += ch
        if len(cur) >= max_chars_per_line:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return "\n".join(out[:3])


def _expand_subtitle_lines(lines: list[str], max_chars_per_line: int) -> list[str]:
    """把长句拆成逐行短句，便于“读完一行就切下一行”显示。"""
    out: list[str] = []
    for raw in lines:
        t = str(raw or "").strip()
        if not t:
            continue
        wrapped = _wrap_caption_text(t, max_chars_per_line)
        parts = [x.strip() for x in wrapped.split("\n") if x.strip()]
        out.extend(parts or [t])
    return out or ["正在生成字幕…"]


def _line_weight(text: str) -> float:
    t = str(text or "").strip()
    if not t:
        return 1.0
    # 句长为主，标点稍微加权，模拟自然停顿。
    punct = len(re.findall(r"[，。！？；,.!?;:：]", t))
    return max(1.0, len(t) + punct * 4)


def _allocate_line_durations(lines: list[str], total_duration: float) -> list[float]:
    n = len(lines)
    if n <= 0:
        return []
    if total_duration <= 0:
        return [1.0] * n
    min_seg = 0.9
    max_seg = 4.2
    if n * min_seg >= total_duration:
        each = total_duration / n
        return [each] * n
    weights = [_line_weight(x) for x in lines]
    sw = sum(weights) or float(n)
    base = [total_duration * (w / sw) for w in weights]
    dur = [min(max(x, min_seg), max_seg) for x in base]
    cur = sum(dur)
    diff = total_duration - cur
    if abs(diff) < 1e-6:
        return dur
    if diff > 0:
        grow_idx = sorted(range(n), key=lambda i: weights[i], reverse=True)
        k = 0
        while diff > 1e-6 and k < n * 3:
            i = grow_idx[k % n]
            room = max_seg - dur[i]
            if room > 1e-6:
                add = min(room, diff)
                dur[i] += add
                diff -= add
            k += 1
    else:
        shrink_idx = sorted(range(n), key=lambda i: weights[i])
        diff = -diff
        k = 0
        while diff > 1e-6 and k < n * 3:
            i = shrink_idx[k % n]
            room = dur[i] - min_seg
            if room > 1e-6:
                sub = min(room, diff)
                dur[i] -= sub
                diff -= sub
            k += 1
    return dur


async def _run_ffmpeg(args: list[str], timeout_sec: float = 70.0) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"ffmpeg timeout after {timeout_sec:.0f}s")
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
_VIDEO_META_RE = re.compile(
    r'<meta[^>]+property=["\'](?:og:video|og:video:url|twitter:player:stream)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_VIDEO_TAG_RE = re.compile(r'<video[^>]+src=["\']([^"\']+)["\']', re.I)
_VIDEO_SOURCE_RE = re.compile(r'<source[^>]+src=["\']([^"\']+)["\']', re.I)
_VIDEO_URL_RE = re.compile(r'https?:\/\/[^\s"\'<>]+(?:\.m3u8|\.mp4|\.webm)[^\s"\'<>]*', re.I)


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


async def _create_fallback_carousel_slides(job_dir: Path, count: int) -> list[Path]:
    """无新闻图/视频时：用 ffmpeg lavfi 生成多帧竖版纯色底图，配合分段渲染形成轮播观感。"""
    n = max(1, min(int(count), 6))
    # 相邻帧略有色差，轮播时不至于完全「死灰一块」
    hex_colors = ("0x171a1f", "0x1e232a", "0x262d36", "0x2e3640", "0x363e4a", "0x3e4755")
    out: list[Path] = []
    for i in range(n):
        c = hex_colors[i % len(hex_colors)]
        p = job_dir / f"fallback_carousel_{i}.jpg"
        await _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={c}:s=720x1280:r=30",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(p),
            ]
        )
        if p.exists():
            out.append(p)
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


async def _collect_article_video_clips(job_dir: Path, meta: dict, limit: int = 2, duration_sec: float = 4.0) -> list[Path]:
    """从新闻正文页提取视频链接并转码为竖屏片段。"""
    article_url = str(meta.get("article_url") or "").strip()
    if not article_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
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
    for pattern in (_VIDEO_META_RE, _VIDEO_TAG_RE, _VIDEO_SOURCE_RE):
        for m in pattern.finditer(html):
            raw = m.group(1).strip()
            if not raw:
                continue
            full = urljoin(article_url, raw)
            low = full.lower()
            if not low.startswith(("http://", "https://")):
                continue
            if not any(k in low for k in (".mp4", ".m3u8", ".webm", "playlist", "manifest")):
                continue
            if full not in urls:
                urls.append(full)
            if len(urls) >= limit * 2:
                break
        if len(urls) >= limit * 2:
            break
    if len(urls) < limit:
        for m in _VIDEO_URL_RE.finditer(html):
            u = m.group(0).strip()
            low = u.lower()
            if not low.startswith(("http://", "https://")):
                continue
            if u not in urls:
                urls.append(u)
            if len(urls) >= limit * 2:
                break

    out: list[Path] = []
    for i, u in enumerate(urls[: limit * 2]):
        p = job_dir / f"article_clip_{i}.mp4"
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
            if len(out) >= limit:
                break
        except Exception:
            continue
    return out


def _split_subtitles(meta: dict) -> list[str]:
    """口播字幕行：与脚本 hook/body/ending 行对齐；不再硬截 26 字/只取 4 行，避免口播与字幕严重不完整。"""
    narration_text = str(meta.get("narration_text") or "").strip()
    if narration_text:
        lines = [x.strip() for x in narration_text.splitlines() if x.strip()]
        if lines:
            tone = str(meta.get("subtitle_tone") or "").strip().lower()
            if tone in {"精简", "brief", "short"}:
                return lines[:8]
            return lines[:14]
    return _build_segments(meta.get("script") or {})[:14]


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
        text = _escape_drawtext(line[:72])
        filters.append(
            "drawtext="
            f"fontfile={font_path}:"
            f"text='{text}':"
            "x=(w-text_w)/2:y=h-162:"
            "fontsize=64:fontcolor=white:borderw=4:bordercolor=black@0.95:shadowx=2:shadowy=2:shadowcolor=black@0.65:"
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
    aspect_ratio = str(meta.get("aspect_ratio") or "9:16").strip()
    if aspect_ratio == "16:9":
        canvas_w, canvas_h = 1280, 720
    else:
        canvas_w, canvas_h = 720, 1280
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
    dedup: list[Path] = []
    seen: set[str] = set()
    for p in bg_images:
        k = str(p.resolve()) if p.exists() else str(p)
        if k not in seen:
            seen.add(k)
            dedup.append(p)
    # 不再强裁到 4 张，避免“勾选了上传+候选但只用了前几张”。
    max_bg = max(len(subtitle_lines), 8)
    bg_images = dedup[:max_bg]
    web_clips: list[str] = []
    for s in list(meta.get("user_video_paths") or []):
        p = Path(str(s))
        if p.exists():
            web_clips.append(str(p))
    for u in list(meta.get("user_video_urls") or []):
        s = str(u or "").strip()
        if s.startswith(("http://", "https://")):
            web_clips.append(s)
    clip_dedup: list[str] = []
    clip_seen: set[str] = set()
    for p in web_clips:
        k = p
        if k not in clip_seen:
            clip_seen.add(k)
            clip_dedup.append(p)
    # 同理放宽视频素材截断，尽量覆盖所有勾选的视频来源。
    max_clip = max(len(subtitle_lines), 8)
    web_clips = clip_dedup[:max_clip]
    mixed_sources: list[tuple[str, str]] = []
    if bg_images and web_clips:
        max_n = max(len(bg_images), len(web_clips))
        for i in range(max_n):
            if prefer_video:
                if i < len(web_clips):
                    mixed_sources.append(("video", web_clips[i]))
                if i < len(bg_images):
                    mixed_sources.append(("image", str(bg_images[i])))
            else:
                if i < len(bg_images):
                    mixed_sources.append(("image", str(bg_images[i])))
                if i < len(web_clips):
                    mixed_sources.append(("video", web_clips[i]))
    if not bg_images and not web_clips:
        if must_uploaded:
            raise RuntimeError("已开启「仅已上传」，但未检测到可用的图片或视频素材，已停止渲染。")
        # 画布兜底：多帧竖版占位图 + 字幕分段，等价于无图时的「轮播底」
        slide_count = max(len(subtitle_lines), 3)
        bg_images = await _create_fallback_carousel_slides(job_dir, slide_count)
        if not bg_images:
            raise RuntimeError("未获取到新闻素材，且无法生成画布兜底帧（请检查本机 ffmpeg 是否支持 lavfi）。")

    max_chars = 17 if aspect_ratio == "16:9" else 12
    display_lines = _expand_subtitle_lines(subtitle_lines, max_chars)

    line_durations = _allocate_line_durations(display_lines, duration)
    srt_lines: list[str] = []
    cursor = 0.0
    for i, line in enumerate(display_lines, start=1):
        seg = line_durations[i - 1] if i - 1 < len(line_durations) else 1.0
        st = _fmt_ts(cursor)
        cursor = min(duration, cursor + seg)
        ed = _fmt_ts(cursor)
        srt_lines.append(f"{i}\n{st} --> {ed}\n{line}\n")
    subtitle_path.write_text("\n".join(srt_lines), encoding="utf-8")

    selected_asset_count = max(len(bg_images), 0) + max(len(web_clips), 0)
    part_count = max(len(display_lines), selected_asset_count, 1)
    seg_durations = _allocate_line_durations(
        [display_lines[min((i * len(display_lines)) // part_count, len(display_lines) - 1)] if display_lines else "" for i in range(part_count)],
        duration,
    )
    part_files: list[Path] = []
    for i in range(part_count):
        line = display_lines[min((i * len(display_lines)) // part_count, len(display_lines) - 1)] if display_lines else ""
        seg_dur = seg_durations[i] if i < len(seg_durations) else (duration / part_count)
        part = job_dir / f"part_{i}.mp4"
        part_files.append(part)
        subtitle_text = _escape_drawtext(line[:72])
        top_h = int(canvas_h * 0.09)
        # 播放器底部控制条在预览时会遮挡画面底部；字幕区整体再上移，分比例做不同安全边距。
        if aspect_ratio == "16:9":
            bottom_h = int(canvas_h * 0.24)
            safe_bottom = int(canvas_h * 0.11)
        else:
            bottom_h = int(canvas_h * 0.24)
            safe_bottom = int(canvas_h * 0.10)
        bottom_y = canvas_h - bottom_h - safe_bottom
        src_font = max(24, int(canvas_h * 0.03))
        sub_font = max(30, int(canvas_h * 0.036))
        sub_y = bottom_y + int(bottom_h * 0.50)
        head_overlay = (
            f"drawbox=x=0:y=0:w=iw:h={top_h}:color=black@0.35:t=fill,"
            f"drawtext=fontfile={font}:text='{source}':x=24:y=20:fontsize={src_font}:fontcolor=white:borderw=1:bordercolor=black@0.7,"
            f"drawbox=x=0:y={bottom_y}:w=iw:h={bottom_h}:color=black@0.56:t=fill,"
            f"drawtext=fontfile={font}:text='{subtitle_text}':x=max(20\\,(w-text_w)/2):y={sub_y}:fontsize={sub_font}:line_spacing=4:fontcolor=white:borderw=4:bordercolor=black@0.95:shadowx=2:shadowy=2:shadowcolor=black@0.65,"
            "format=yuv420p"
        )

        if mixed_sources:
            kind, src = mixed_sources[i % len(mixed_sources)]
            if kind == "video":
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    src,
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
                continue
            img = Path(src)
            vf = (
                f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,"
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
            await _run_ffmpeg(cmd)
        elif bg_images:
            img = bg_images[i % len(bg_images)]
            vf = (
                f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,"
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
            await _run_ffmpeg(cmd)
        else:
            clip = web_clips[i % len(web_clips)]
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                clip,
                "-t",
                f"{seg_dur:.2f}",
                "-vf",
                f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,eq=brightness=-0.03:saturation=1.08,{head_overlay}",
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
