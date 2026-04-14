import axios from 'axios'

import type { JobDetailResponse } from '@/types/job'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: 120_000,
})

export interface CommandResponse {
  reply: string
  active_job_id?: number | null
  candidates?: Array<{
    index: number
    title: string
    title_zh?: string | null
    source: string
    score_10?: number | null
    heat_index?: number | null
    source_weight?: number | null
    recency_score_10?: number | null
    rank_score_10?: number | null
    published_at: string | null
    summary: string | null
    summary_zh?: string | null
    tier: string
    url: string
  }>
  job?: { id: number; status: string }
}

export async function postCommand(message: string, activeJobId: number | null): Promise<CommandResponse> {
  const { data } = await client.post<CommandResponse>('/commands', {
    message,
    active_job_id: activeJobId,
  })
  return data
}

export async function triggerIngest(): Promise<{
  added: number
  refreshed?: number
  sources: number
  failed_count?: number
  failed?: Array<{ source: string; rss_url: string; error: string }>
  hot_top?: Array<{ index: number; source: string; title: string; score_10?: number | null; heat_index?: number | null; tier: string }>
  candidates?: CommandResponse['candidates']
}> {
  const { data } = await client.post<{
    added: number
    refreshed?: number
    sources: number
    failed_count?: number
    failed?: Array<{ source: string; rss_url: string; error: string }>
    hot_top?: Array<{ index: number; source: string; title: string; score_10?: number | null; heat_index?: number | null; tier: string }>
    candidates?: CommandResponse['candidates']
  }>('/ingest/trigger')
  return data
}

export async function fetchJobDetail(jobId: number): Promise<JobDetailResponse> {
  const { data } = await client.get<JobDetailResponse>(`/jobs/${jobId}/detail`)
  return data
}

export async function createJobFromCandidate(index: number): Promise<{ job: { id: number; status: string } }> {
  const { data } = await client.post<{ job: { id: number; status: string } }>(`/jobs/from-candidate?index=${index}`)
  return data
}

export async function triggerPipeline(jobId: number): Promise<{ ok: boolean; job_id: number }> {
  const { data } = await client.post<{ ok: boolean; job_id: number }>(`/jobs/${jobId}/pipeline`)
  return data
}

export async function triggerRerender(
  jobId: number,
  payload: {
    instruction?: string
    duration_sec?: number
    style_notes?: string
    must_use_uploaded_assets?: boolean
    prefer_video_assets?: boolean
    subtitle_tone?: string
    tts_voice?: string
  },
): Promise<{ ok: boolean; job_id: number; note: string }> {
  const { data } = await client.post<{ ok: boolean; job_id: number; note: string }>(`/jobs/${jobId}/rerender`, payload)
  return data
}

export async function previewTtsVoice(voice: string, text?: string): Promise<Blob> {
  const { data } = await client.post(
    '/tts/preview',
    { voice, text },
    {
      responseType: 'blob',
      timeout: 60_000,
    },
  )
  return data as Blob
}

export async function uploadJobAssets(
  jobId: number,
  files: File[],
): Promise<{ ok: boolean; job_id: number; added: number; files: Array<{ name: string; asset_type: string }> }> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  const { data } = await client.post<{ ok: boolean; job_id: number; added: number; files: Array<{ name: string; asset_type: string }> }>(
    `/jobs/${jobId}/assets/upload`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}
