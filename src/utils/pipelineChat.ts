import type { JobDetailResponse } from '@/types/job'

/** 根据刚完成的阶段与当前详情快照，生成对话正文 */
export function buildPipelineStageBody(completedStageKey: string, d: JobDetailResponse): string {
  const script = d.latest_script
  const lines: string[] = []

  switch (completedStageKey) {
    case 'created':
      lines.push('任务已创建，等待调度。')
      break
    case 'fetching_news':
      lines.push('新闻上下文已就绪。')
      break
    case 'extracting_content': {
      const n = d.content_chars
      if (n != null && n > 0) {
        lines.push(`正文已抽取，约 ${n} 字可用。`)
      } else {
        lines.push('正文已抽取（已用 RSS 摘要等降级填充时可忽略字数）。')
      }
      if (d.news_title) lines.push(`稿件：${d.news_title.slice(0, 120)}${d.news_title.length > 120 ? '…' : ''}`)
      break
    }
    case 'scoring_candidate': {
      const s = d.candidate_score_10
      const t = d.candidate_tier
      if (s != null) {
        lines.push(`候选综合分：${s} / 10`)
      } else {
        lines.push('候选评分阶段已完成（暂无分项分时以人工选题为准）。')
      }
      if (t) lines.push(`档位：${t}`)
      break
    }
    case 'generating_script': {
      if (script && typeof script === 'object') {
        const hook = String(script.hook || '').trim()
        if (hook) lines.push(`口播开头：${hook}`)
        const body = script.body
        if (Array.isArray(body) && body.length) {
          lines.push('要点：')
          body.slice(0, 3).forEach((b, i) => lines.push(`  ${i + 1}. ${String(b).slice(0, 200)}`))
        }
        const titles = script.titles
        if (Array.isArray(titles) && titles.length) {
          lines.push('标题备选：')
          titles.slice(0, 3).forEach((t) => lines.push(`  · ${String(t).slice(0, 80)}`))
        }
      } else {
        lines.push('脚本已写入（详情拉取中可稍后展开）。')
      }
      break
    }
    case 'collecting_assets':
      lines.push('素材收集完成（主图 / 目录已准备）。')
      break
    case 'generating_audio': {
      const a0 = d.audios[0]
      if (a0?.duration_sec != null) {
        lines.push(`配音已生成，时长约 ${Number(a0.duration_sec).toFixed(1)} 秒。`)
      } else {
        lines.push('配音已生成。')
      }
      break
    }
    case 'building_timeline': {
      const cues = d.subtitle_cues
      if (Array.isArray(cues) && cues.length) {
        lines.push('字幕时间轴（句级）：')
        cues.slice(0, 12).forEach((c, i) => {
          const text = String((c as { text?: string }).text || '').trim()
          const start = (c as { start?: number }).start
          const end = (c as { end?: number }).end
          const ts =
            typeof start === 'number' && typeof end === 'number'
              ? `[${start.toFixed(1)}s → ${end.toFixed(1)}s]`
              : `[#${i + 1}]`
          if (text) lines.push(`  ${ts} ${text}`)
        })
        if (cues.length > 12) lines.push(`  …共 ${cues.length} 条`)
      } else {
        lines.push('字幕时间轴已生成（暂无预览文本）。')
      }
      break
    }
    case 'rendering_video':
      lines.push(`视频渲染完成（当前 ${d.videos.length} 个成片记录）。`)
      break
    case 'ready_for_review':
      lines.push('流水线已全部完成，可预览右侧成片或下载。')
      break
    case 'approved':
      lines.push('任务已标记为批准。')
      break
    default:
      lines.push(`阶段「${completedStageKey}」已完成。`)
  }

  return lines.join('\n')
}
