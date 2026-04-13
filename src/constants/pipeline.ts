/** 与后端 JobStatus / pipeline 顺序一致，用于 GitLab 式流水线 UI */

export const PIPELINE_STAGES = [
  { key: 'created', label: '创建' },
  { key: 'extracting_content', label: '正文抽取' },
  { key: 'scoring_candidate', label: '候选评分' },
  { key: 'generating_script', label: '脚本生成' },
  { key: 'collecting_assets', label: '素材收集' },
  { key: 'generating_audio', label: '配音合成' },
  { key: 'building_timeline', label: '字幕时间轴' },
  { key: 'rendering_video', label: '视频渲染' },
  { key: 'ready_for_review', label: '待审核' },
  { key: 'approved', label: '已批准' },
] as const

export type PipelineStageKey = (typeof PIPELINE_STAGES)[number]['key']

export type StageVisualState = 'pending' | 'running' | 'success' | 'failed' | 'skipped'

const KEYS = PIPELINE_STAGES.map((s) => s.key)

const SUCCESS_DISPLAY = new Set<string>(['ready_for_review', 'approved'])

export function computeStageStates(status: string, failedStage: string | null | undefined): StageVisualState[] {
  if (status === 'created') {
    return KEYS.map(() => 'pending') as StageVisualState[]
  }

  if (status === 'failed') {
    const fi = failedStage ? KEYS.indexOf(failedStage as PipelineStageKey) : -1
    if (fi < 0) {
      return KEYS.map(() => 'pending') as StageVisualState[]
    }
    return KEYS.map((_, i) => {
      if (i < fi) return 'success'
      if (i === fi) return 'failed'
      return 'skipped'
    }) as StageVisualState[]
  }

  if (status === 'approved') {
    return KEYS.map(() => 'success') as StageVisualState[]
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
