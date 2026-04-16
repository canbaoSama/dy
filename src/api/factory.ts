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

export interface CandidateLite {
  index: number
  title: string
  title_zh?: string | null
  source: string
  published_at: string | null
  summary: string | null
  summary_zh?: string | null
  tier: string
  url: string
  score_10?: number | null
  heat_index?: number | null
  source_weight?: number | null
  recency_score_10?: number | null
  rank_score_10?: number | null
}

export async function postCommand(message: string, activeJobId: number | null): Promise<CommandResponse> {
  const { data } = await client.post<CommandResponse>('/commands', {
    message,
    active_job_id: activeJobId,
  })
  return data
}

export async function translateCandidatesToZh(
  items: CandidateLite[],
): Promise<Array<{ index: number; url: string; title_zh: string; summary_zh: string }>> {
  const { data } = await client.post<{ items: Array<{ index: number; url: string; title_zh: string; summary_zh: string }> }>(
    '/candidates/translate',
    {
      items: items.map((x) => ({
        index: x.index,
        title: x.title_zh || x.title,
        summary: x.summary_zh || x.summary,
        source: x.source,
        url: x.url,
      })),
    },
  )
  return data.items || []
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

export async function fetchJobAssetCandidates(
  jobId: number,
): Promise<{ job_id: number; article_url: string; items: Array<{ url: string; asset_type: string; source?: string }> }> {
  const { data } = await client.get<{ job_id: number; article_url: string; items: Array<{ url: string; asset_type: string; source?: string }> }>(
    `/jobs/${jobId}/asset-candidates`,
  )
  return data
}

export async function pickRemoteAssets(
  jobId: number,
  items: Array<{ url: string; asset_type?: string }>,
): Promise<{ ok: boolean; job_id: number; added: number }> {
  const { data } = await client.post<{ ok: boolean; job_id: number; added: number }>(`/jobs/${jobId}/assets/select-remote`, { items })
  return data
}

export async function updateLatestSubtitles(
  jobId: number,
  cues: Array<{ start: number; end: number; text: string }>,
): Promise<{ ok: boolean; job_id: number; count: number }> {
  const { data } = await client.put<{ ok: boolean; job_id: number; count: number }>(`/jobs/${jobId}/subtitles/latest`, { cues })
  return data
}
