import type { JobDetailResponse } from '@/types/job'

export type JobArtifactKind = 'script' | 'subtitles' | 'audios' | 'videos'

const MAX_SCRIPT_CHARS = 14_000

export function jobHasScript(d: JobDetailResponse): boolean {
  const s = d.latest_script
  return s != null && typeof s === 'object' && Object.keys(s as object).length > 0
}

export function jobHasSubtitles(d: JobDetailResponse): boolean {
  return Array.isArray(d.subtitle_cues) && d.subtitle_cues.length > 0
}

export function jobHasAudios(d: JobDetailResponse): boolean {
  return d.audios.length > 0
}

export function jobHasVideos(d: JobDetailResponse): boolean {
  return d.videos.length > 0
}

export function formatJobArtifact(kind: JobArtifactKind, d: JobDetailResponse): string {
  const jid = d.job.id
  switch (kind) {
    case 'script': {
      const v = d.latest_script_version
      const head = `【脚本 · 任务 #${jid} · v${v ?? '?'}】`
      if (!jobHasScript(d)) {
        return `${head}\n（暂无脚本 JSON，请等「脚本生成」完成后再点。）`
      }
      let body = JSON.stringify(d.latest_script, null, 2)
      if (body.length > MAX_SCRIPT_CHARS) {
        body = `${body.slice(0, MAX_SCRIPT_CHARS)}\n\n…（已截断）`
      }
      return `${head}\n${body}`
    }
    case 'subtitles': {
      const head = `【字幕表 · 任务 #${jid}】`
      const cues = d.subtitle_cues
      if (!cues?.length) {
        const hint =
          d.subtitle_timeline_id != null
            ? `（已关联时间轴 #${d.subtitle_timeline_id}，但条目尚未返回，可点「刷新状态」后重试。）`
            : '（暂无字幕条目，请等管线生成字幕后再试。）'
        return `${head}\n${hint}`
      }
      const lines = cues.map((c, i) => {
        const idx = c.index ?? i + 1
        const a = c.start != null ? `${Number(c.start).toFixed(2)}` : '?'
        const b = c.end != null ? `${Number(c.end).toFixed(2)}` : '?'
        const t = (c.text || '').trim() || '（空）'
        return `${idx}. [${a}s → ${b}s] ${t}`
      })
      return `${head} 共 ${cues.length} 条\n${lines.join('\n')}`
    }
    case 'audios': {
      const head = `【音频产物 · 任务 #${jid}】`
      if (!d.audios.length) {
        return `${head}\n（暂无记录。）`
      }
      return (
        `${head} 共 ${d.audios.length} 条\n` +
        d.audios
          .map((a, i) => `${i + 1}. id=${a.id} · ${a.duration_sec ?? '?'}s\n   ${a.file_path}`)
          .join('\n')
      )
    }
    case 'videos': {
      const head = `【视频产物 · 任务 #${jid}】`
      if (!d.videos.length) {
        return `${head}\n（暂无成片记录。）`
      }
      return (
        `${head} 共 ${d.videos.length} 条\n` +
        d.videos
          .map((v, i) => {
            const meta = v.meta_json ? `\n   meta: ${JSON.stringify(v.meta_json)}` : ''
            return `${i + 1}. id=${v.id}\n   file: ${v.file_path ?? '—'}\n   preview: ${v.preview_path ?? '—'}${meta}`
          })
          .join('\n\n')
      )
    }
  }
}
