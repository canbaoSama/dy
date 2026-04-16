/** 与后端 JobStatus / pipeline 顺序一致，用于 GitLab 式流水线 UI */

export const PIPELINE_STAGES = [
  { key: 'created', label: '创建' },
  { key: 'extracting_content', label: '正文抽取' },
  { key: 'collecting_assets', label: '素材收集' },
  { key: 'generating_subtitles', label: '字幕生成' },
  { key: 'generating_script', label: '脚本生成' },
  { key: 'building_timeline', label: '字幕时间轴' },
  { key: 'generating_audio', label: '配音合成' },
  { key: 'rendering_video', label: '视频渲染' },
  { key: 'ready_for_review', label: '完成' },
] as const

export type PipelineStageKey = (typeof PIPELINE_STAGES)[number]['key']

export type StageVisualState = 'pending' | 'running' | 'success' | 'failed' | 'skipped'

const KEYS = PIPELINE_STAGES.map((s) => s.key)

const SUCCESS_DISPLAY = new Set<string>(['ready_for_review'])

export function computeStageStates(status: string, failedStage: string | null | undefined): StageVisualState[] {
  if (status === 'created') {
    return KEYS.map(() => 'pending') as StageVisualState[]
  }

  if (status === 'failed') {
    const fi = failedStage ? KEYS.indexOf(failedStage as PipelineStageKey) : -1
    if (fi < 0) {
      // 后端未带回 failed_stage 时：避免整条全灰像「全挂」，默认标在「视频渲染」
      const fallback = KEYS.indexOf('rendering_video')
      const j = fallback >= 0 ? fallback : KEYS.length - 1
      return KEYS.map((_, i) => {
        if (i < j) return 'success'
        if (i === j) return 'failed'
        return 'skipped'
      }) as StageVisualState[]
    }
    return KEYS.map((_, i) => {
      if (i < fi) return 'success'
      if (i === fi) return 'failed'
      return 'skipped'
    }) as StageVisualState[]
  }

  const idx = KEYS.indexOf(status as PipelineStageKey)
  if (idx < 0) {
    return KEYS.map(() => 'pending') as StageVisualState[]
  }

  return KEYS.map((_, i) => {
    if (i < idx) return 'success'
    if (i > idx) return 'pending'
    if (SUCCESS_DISPLAY.has(status)) return 'success'
    return 'running'
  }) as StageVisualState[]
}

export function connectorAfterState(left: StageVisualState): 'done' | 'active' | 'idle' | 'bad' | 'skip' {
  if (left === 'success') return 'done'
  if (left === 'running') return 'active'
  if (left === 'failed') return 'bad'
  if (left === 'skipped') return 'skip'
  return 'idle'
}

/** 对话里流水线卡片的图标与色系（与 PIPELINE_STAGES.key 对齐） */
export type PipelineAccent = 'slate' | 'violet' | 'amber' | 'emerald' | 'sky' | 'rose' | 'cyan' | 'fuchsia' | 'orange'

export function pipelineStageLabel(key: string): string {
  return PIPELINE_STAGES.find((s) => s.key === key)?.label ?? key
}

export function pipelineStageUi(key: string): { label: string; icon: string; accent: PipelineAccent } {
  const label = pipelineStageLabel(key)
  const map: Record<string, { icon: string; accent: PipelineAccent }> = {
    created: { icon: '📋', accent: 'slate' },
    fetching_news: { icon: '📡', accent: 'sky' },
    extracting_content: { icon: '📄', accent: 'cyan' },
    scoring_candidate: { icon: '⭐', accent: 'amber' },
    generating_script: { icon: '✍️', accent: 'violet' },
    collecting_assets: { icon: '🖼️', accent: 'fuchsia' },
    generating_subtitles: { icon: '📝', accent: 'amber' },
    generating_audio: { icon: '🎙️', accent: 'rose' },
    building_timeline: { icon: '💬', accent: 'sky' },
    rendering_video: { icon: '🎬', accent: 'emerald' },
    ready_for_review: { icon: '✅', accent: 'emerald' },
    approved: { icon: '🏁', accent: 'emerald' },
    failed: { icon: '⚠️', accent: 'orange' },
  }
  const m = map[key] || { icon: '⚙️', accent: 'slate' as PipelineAccent }
  return { label, icon: m.icon, accent: m.accent }
}

export function pipelineStageIndex(key: string): number {
  return PIPELINE_STAGES.findIndex((s) => s.key === key)
}
