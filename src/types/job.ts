export interface JobOut {
  id: number
  news_item_id: number
  status: string
  duration_sec: number
  style_notes: string | null
  error_message: string | null
  failed_stage?: string | null
  created_at: string
  updated_at?: string | null
}

export interface SubtitleCue {
  index?: number
  start?: number
  end?: number
  text?: string
}

export interface JobDetailResponse {
  job: JobOut
  latest_script: Record<string, unknown> | null
  latest_script_version: number | null
  audios: Array<{ id: number; file_path: string; duration_sec: number | null; meta_json?: Record<string, unknown> | null }>
  videos: Array<{
    id: number
    file_path: string | null
    preview_path: string | null
    meta_json?: Record<string, unknown> | null
  }>
  subtitle_timeline_id: number | null
  news_title?: string | null
  content_chars?: number | null
  candidate_score_10?: number | null
  candidate_tier?: string | null
  subtitle_cues?: SubtitleCue[] | null
}
