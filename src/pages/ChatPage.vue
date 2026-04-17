<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { v4 as uuidv4 } from 'uuid'
import { ElMessage } from 'element-plus'
import JobStatusPanel from '@/components/JobStatusPanel.vue'
import { AGENT_NAME } from '@/config/app'
import { QUICK_COMMANDS } from '@/constants/commands'
import { PIPELINE_STAGES, pipelineStageIndex, pipelineStageUi } from '@/constants/pipeline'
import { TTS_VOICE_OPTIONS } from '@/constants/tts'
import { fetchJobDetail, postCommand, previewTtsVoice, updateLatestSubtitles } from '@/api/factory'
import { useChatStore } from '@/store/chat'
import type { ChatMessage } from '@/types/chat'
import type { JobDetailResponse } from '@/types/job'
import { expandUrlsInRichSegments, splitAssistantRichSegments } from '@/utils/assistantRichText'
import { formatJobArtifact, type JobArtifactKind } from '@/utils/jobArtifactChat'
import { buildPipelineStageBody } from '@/utils/pipelineChat'

const chatStore = useChatStore()
/** 最近一次任务详情（与右侧 Job 面板同源），用于「脚本 / 字幕」等快捷展示 */
const lastJobDetail = ref<JobDetailResponse | null>(null)
/** 各任务上一次轮询到的状态，用于在对话里推送「阶段完成」卡片 */
const lastPollStatus = ref<Record<number, string | undefined>>({})
/** 各任务已推送过的阶段，防止重复补发 */
const pushedStages = ref<Record<number, string[]>>({})
/** 主对话区独立轮询 generation；递增后旧轮询立即退出 */
const jobDetailPollGen = ref(0)
const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const uploadRef = ref<HTMLInputElement | null>(null)
const rerenderForm = reactive({
  duration_sec: 18,
  subtitle_tone: 'brief',
  tts_voice: 'zh-CN-XiaoxiaoNeural',
  aspect_ratio: '9:16' as '9:16' | '16:9',
  must_use_uploaded_assets: false,
  prefer_video_assets: true,
})

const canSend = computed(() => input.value.trim().length > 0 && !chatStore.loading)
const previewingVoice = ref(false)
const previewReqSeq = ref(0)
let previewAudio: HTMLAudioElement | null = null
const flowCardMode = ref<'compact' | 'detail'>('compact')
const pickedAssetMap = ref<Record<string, number[]>>({})
const subtitleDraftMap = ref<Record<string, string>>({})
const uploadPreviewMap = ref<Record<string, Array<{ name: string; objectUrl: string; media: 'image' | 'video' }>>>({})
const activeAssetCardId = ref<string | null>(null)
const pendingUploadMap = ref<
  Record<string, Array<{ id: string; name: string; objectUrl: string; media: 'image' | 'video'; file: File }>>
>({})
const pickedUploadMap = ref<Record<string, string[]>>({})
const SLASH_COMMANDS = [
  {
    command: '/sucai',
    description: '在对话中加载素材候选，可勾选/上传后一键重新成片',
    template: '/sucai ',
  },
  {
    command: '/zimu',
    description: '在对话中打开上一条字幕并编辑，确定后重新成片',
    template: '/zimu ',
  },
] as const
const CHAT_OWNED_STAGES = new Set([
  'collecting_assets',
  'generating_subtitles',
  'generating_script',
  'building_timeline',
  'generating_audio',
  'rendering_video',
  'ready_for_review',
])

const artifactQuickItems = computed(() => {
  const d = lastJobDetail.value
  const jid = chatStore.activeJobId
  if (d == null || jid == null || d.job.id !== jid) return []
  return [
    { kind: 'script' as const, label: '脚本' },
    { kind: 'subtitles' as const, label: '字幕' },
    { kind: 'audios' as const, label: '音频' },
    { kind: 'videos' as const, label: '视频' },
  ] as const
})

const slashKeyword = computed(() => {
  const t = input.value.trimStart()
  if (!t.startsWith('/')) return ''
  return t.slice(1).toLowerCase()
})

const slashCommandOptions = computed(() => {
  const key = slashKeyword.value
  if (!key) return [...SLASH_COMMANDS]
  return SLASH_COMMANDS.filter((x) => x.command.slice(1).toLowerCase().includes(key))
})

const showSlashPanel = computed(() => input.value.trimStart().startsWith('/') && slashCommandOptions.value.length > 0)

async function sendText(text: string) {
  const v = text.trim()
  if (!v || chatStore.loading) return
  input.value = ''
  await chatStore.ask(v)
}

async function scrollToBottom() {
  await nextTick()
  const el = listRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => chatStore.messages.length,
  () => {
    void scrollToBottom()
  },
)

watch(
  () => rerenderForm.tts_voice,
  async (voice, prev) => {
    const v = String(voice || '').trim()
    if (!v || !prev || v === prev) return
    const reqId = ++previewReqSeq.value
    previewingVoice.value = true
    try {
      const blob = await previewTtsVoice(v)
      if (reqId !== previewReqSeq.value) return
      if (previewAudio) {
        previewAudio.pause()
        previewAudio = null
      }
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      previewAudio = audio
      audio.onended = () => {
        URL.revokeObjectURL(url)
        if (previewAudio === audio) previewAudio = null
      }
      audio.onerror = () => {
        URL.revokeObjectURL(url)
      }
      await audio.play()
    } catch (e) {
      if (reqId === previewReqSeq.value) {
        ElMessage.warning(e instanceof Error ? e.message : '音色试听失败')
      }
    } finally {
      if (reqId === previewReqSeq.value) previewingVoice.value = false
    }
  },
)

async function onSend() {
  const raw = input.value.trim()
  if (!raw) return
  if (await handleSlashCommand(raw)) {
    input.value = ''
    return
  }
  await sendText(raw)
}

function pickSlashCommand(template: string) {
  input.value = template
}

function onClear() {
  for (const arr of Object.values(uploadPreviewMap.value)) {
    for (const u of arr) {
      try {
        URL.revokeObjectURL(u.objectUrl)
      } catch {
        /* ignore */
      }
    }
  }
  for (const arr of Object.values(pendingUploadMap.value)) {
    for (const u of arr) {
      try {
        URL.revokeObjectURL(u.objectUrl)
      } catch {
        /* ignore */
      }
    }
  }
  uploadPreviewMap.value = {}
  pendingUploadMap.value = {}
  pickedUploadMap.value = {}
  jobDetailPollGen.value += 1
  // 保留 lastPollStatus，避免清空会话后下一轮轮询又把整条流水线重复推一遍
  chatStore.clear()
  pushedStages.value = {}
}

function assistantSegments(item: ChatMessage) {
  if (item.role !== 'assistant' || item.bubbleKind === 'pipeline') {
    return [{ type: 'text' as const, text: item.content }]
  }
  // 候选消息：标题本身可点击，不单独显示 URL 文本
  if (item.content.startsWith('今日全球热点候选（抓取后自动更新）：')) {
    const out: Array<{ type: 'index' | 'source' | 'text' | 'link'; text: string }> = []
    const lines = item.content.split('\n')
    for (let i = 0; i < lines.length; i++) {
      if (i > 0) out.push({ type: 'text', text: '\n' })
      if (i === 0) {
        out.push({ type: 'text', text: lines[i] })
        continue
      }
      const m = lines[i].match(/^(\d+\.\s*)(【[^】]*】)(.*?)(（🔥[^）]*）)?$/)
      if (!m) {
        out.push({ type: 'text', text: lines[i] })
        continue
      }
      out.push({ type: 'index', text: m[1] })
      out.push({ type: 'source', text: m[2] })
      const titleText = m[3] || ''
      const idx = Number(m[1].replace('.', '').trim())
      const url = chatStore.lastCandidates?.find((x) => x.index === idx)?.url?.trim()
      if (url && titleText.trim()) {
        out.push({ type: 'link', text: `${titleText}|||${url}` })
      } else {
        out.push({ type: 'text', text: titleText })
      }
      if (m[4]) out.push({ type: 'text', text: m[4] })
    }
    return out
  }
  // 通用兜底：若出现“候选行 + 下一行裸 URL”，隐藏 URL 行并把候选行做成可点击链接
  if (item.content.includes('http://') || item.content.includes('https://')) {
    const out: Array<{ type: 'index' | 'source' | 'text' | 'link'; text: string }> = []
    const lines = item.content.split('\n')
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]
      const next = lines[i + 1] ?? ''
      const nextUrl = next.match(/^\s*(https?:\/\/\S+)\s*$/)
      const cur = line.match(/^(\d+\.\s*)(.+)$/)
      if (cur && nextUrl) {
        if (out.length) out.push({ type: 'text', text: '\n' })
        out.push({ type: 'index', text: cur[1] })
        out.push({ type: 'link', text: `${cur[2]}|||${nextUrl[1]}` })
        i += 1
        continue
      }
      if (out.length) out.push({ type: 'text', text: '\n' })
      for (const b of splitAssistantRichSegments(line)) {
        out.push(b)
      }
    }
    return out
  }
  return expandUrlsInRichSegments(splitAssistantRichSegments(item.content))
}

function parseAssetCandidates(text: string): Array<{ index: number; type: 'image' | 'video'; url: string }> {
  if (!text.includes('素材候选如下')) return []
  const out: Array<{ index: number; type: 'image' | 'video'; url: string }> = []
  for (const line of text.split('\n')) {
    const m = line.match(/^(\d+)\.\s*\[(图片|视频)\]\s*(https?:\/\/\S+)$/)
    if (!m) continue
    out.push({
      index: Number(m[1]),
      type: m[2] === '视频' ? 'video' : 'image',
      url: m[3],
    })
  }
  return out
}

function isAssetCandidateMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && item.content.includes('素材候选如下')
}

function isUploadPreviewMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && Boolean(item.uploadPreviews?.length)
}

async function onPickAssetByIndex(index: number) {
  await sendText(`选素材 ${index}`)
}

function toggleAssetPick(messageId: string, index: number) {
  const current = new Set(pickedAssetMap.value[messageId] || [])
  if (current.has(index)) current.delete(index)
  else current.add(index)
  pickedAssetMap.value[messageId] = Array.from(current).sort((a, b) => a - b)
}

async function submitPickedAssets(messageId: string) {
  const jid = chatStore.activeJobId
  if (!jid) {
    ElMessage.warning('请先选择任务')
    return
  }
  const picked = pickedAssetMap.value[messageId] || []
  const pickedUploadIds = new Set(pickedUploadMap.value[messageId] || [])
  const pendingUploads = pendingUploadMap.value[messageId] || []
  const pickedUploads = pendingUploads.filter((x) => pickedUploadIds.has(x.id))
  if (pendingUploads.length > 0 && pickedUploads.length === 0) {
    ElMessage.warning('已上传素材未勾选，请先选择要使用的上传素材')
    return
  }
  if (picked.length === 0 && pickedUploads.length === 0) {
    ElMessage.warning('请先选择至少一个候选素材或上传附件')
    return
  }
  if (pickedUploads.length > 0) {
    await chatStore.uploadAssets(
      pickedUploads.map((x) => x.file),
      { silentMessage: true, replaceExisting: true },
    )
  }
  try {
    // “选素材”回包文案不含附件数量，这里手动执行并吞掉原始回包，统一由前端输出一条合并提示。
    if (picked.length) {
      const r = await postCommand(`选素材 ${picked.join(',')}`, jid)
      if (r.active_job_id != null) chatStore.activeJobId = r.active_job_id
    }
    await chatStore.runAssistantCommands(jid, ['生成字幕'])
    chatStore.appendAssistantMessage({
      id: uuidv4(),
      role: 'assistant',
      content: `素材确认完成：候选 ${picked.length} 个，附件 ${pickedUploads.length} 个。字幕已生成，请按流程继续：确认字幕 -> 生成音频 -> 确认音频 -> 合成视频。`,
      createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    })
    uploadPreviewMap.value[messageId] = []
    pendingUploadMap.value[messageId] = []
    pickedUploadMap.value[messageId] = []
    void scrollToBottom()
  } catch {
    /* runAssistantCommands 已提示 */
  }
}

function toggleAllAssets(messageId: string, text: string) {
  const remoteAll = parseAssetCandidates(text).map((x) => x.index)
  const uploadAll = (pendingUploadMap.value[messageId] || []).map((x) => x.id)
  const curRemote = new Set(pickedAssetMap.value[messageId] || [])
  const curUpload = new Set(pickedUploadMap.value[messageId] || [])
  const remoteAllSelected = remoteAll.length === 0 || remoteAll.every((x) => curRemote.has(x))
  const uploadAllSelected = uploadAll.length === 0 || uploadAll.every((x) => curUpload.has(x))
  const isAll = remoteAllSelected && uploadAllSelected && (remoteAll.length + uploadAll.length > 0)
  pickedAssetMap.value[messageId] = isAll ? [] : remoteAll
  pickedUploadMap.value[messageId] = isAll ? [] : uploadAll
}

function getApiOrigin() {
  const base = String(import.meta.env.VITE_API_BASE || '').trim()
  if (!base) return window.location.origin
  try {
    return new URL(base).origin
  } catch {
    return window.location.origin
  }
}

function resolveMediaUrl(url: string) {
  const v = String(url || '').trim()
  if (!v) return ''
  if (/^https?:\/\//i.test(v)) return v
  if (v.startsWith('/')) return `${getApiOrigin()}${v}`
  return `${getApiOrigin()}/${v}`
}

function parseAudioPreviewUrl(text: string): string {
  const m = text.match(/试听地址：\s*(\S+)/)
  return m ? resolveMediaUrl(m[1]) : ''
}

function parseVideoPreviewUrl(text: string): string {
  const m = text.match(/预览地址：\s*(\S+)/)
  return m ? resolveMediaUrl(m[1]) : ''
}

function parseVideoDownloadUrl(text: string): string {
  const m = text.match(/下载地址：\s*(\S+)/)
  return m ? resolveMediaUrl(m[1]) : ''
}

function parseSubtitleJobId(text: string): number | null {
  const m = text.match(/任务：#(\d+)/)
  if (!m) return null
  return Number(m[1])
}

function parseSubtitlePreviewLines(text: string): string[] {
  const m = text.match(/字幕预览：\n([\s\S]*?)\n你可以在对话框中直接编辑字幕并保存/)
  if (!m) return []
  return m[1]
    .split('\n')
    .map((x) => x.replace(/^\-\s*/, '').trim())
    .filter(Boolean)
}

/** 与后端「生成字幕」回包格式一致，便于复用 isSubtitleStepMessage / parseSubtitlePreviewLines */
function buildSubtitleStepReply(jobId: number, lines: string[]): string {
  const preview = lines.map((x) => `- ${x}`).join('\n')
  return (
    `字幕步骤完成，任务：#${jobId}，共 ${lines.length} 条。\n` +
    `字幕预览：\n${preview}\n` +
    `你可以在对话框中直接编辑字幕并保存；确认后发送「确认字幕」继续。`
  )
}

function firstLine(text: string): string {
  return String(text || '').split('\n')[0]?.trim() || ''
}

function compactBodyLines(text: string): string[] {
  return String(text || '')
    .split('\n')
    .slice(1)
    .map((x) => x.trim())
    .filter((x) => x && !x.startsWith('试听地址：') && !x.startsWith('预览地址：') && !x.startsWith('下载地址：'))
}

function displayLines(lines: string[], compactLimit = 3): string[] {
  if (flowCardMode.value === 'detail') return lines
  return lines.slice(0, compactLimit)
}

function isSubtitleStepMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && item.content.startsWith('字幕步骤完成')
}

function isAudioStepMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && item.content.startsWith('音频步骤完成')
}

function isVideoStepMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && item.content.startsWith('视频合成完成')
}

function isTimelineStepMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && item.content.startsWith('字幕时间轴完成')
}

type FlowCardInfo = {
  title: string
  desc?: string
  items?: string[]
}

function parseFlowCard(text: string): FlowCardInfo | null {
  const t = String(text || '').trim()
  if (!t) return null
  if (t.startsWith('步骤 1/2：正文抽取完成')) {
    const title = t.split('\n')[0]
    const lines = t.split('\n').slice(1).map((x) => x.trim()).filter(Boolean)
    return { title, desc: '正文抽取', items: lines }
  }
  if (t.startsWith('已加入') && t.includes('素材')) {
    return { title: t.split('\n')[0], desc: '素材确认' }
  }
  if (t.startsWith('字幕已确认')) {
    return { title: t.split('\n')[0], desc: '字幕确认', items: t.split('\n').slice(1).filter(Boolean) }
  }
  if (t.startsWith('脚本步骤完成')) {
    const rows = t.split('\n').map((x) => x.trim()).filter(Boolean)
    return { title: rows[0] || '脚本生成完成', desc: '脚本生成', items: rows.slice(1, 6) }
  }
  return null
}

function isFlowCardMessage(item: ChatMessage): boolean {
  return item.role === 'assistant' && !!parseFlowCard(item.content)
}

function flowIconByTag(tag?: string): string {
  const t = String(tag || '')
  if (t.includes('正文')) return '📄'
  if (t.includes('素材')) return '🖼️'
  if (t.includes('字幕')) return '📝'
  if (t.includes('脚本')) return '✍️'
  if (t.includes('时间轴')) return '⏱️'
  if (t.includes('音频')) return '🎧'
  if (t.includes('视频')) return '🎬'
  return '✨'
}

function parseTimelineSummary(text: string): {
  title: string
  countText: string
  previews: string[]
  nextStep: string
} {
  const title = text.split('\n')[0]?.trim() || '字幕时间轴完成'
  const count = text.match(/共\s*(\d+)\s*条/)
  const next = text.match(/下一步.*$/m)
  const previews = text
    .split('\n')
    .map((x) => x.trim())
    .filter((x) => x.startsWith('- '))
    .map((x) => x.replace(/^- /, '').trim())
  return {
    title,
    countText: count ? `共 ${count[1]} 条` : '',
    previews: previews.slice(0, 4),
    nextStep: next?.[0] || '下一步将进入后续处理。',
  }
}

/** 从「字幕时间轴完成，任务：#N」类文案解析任务 ID */
function parseTimelineJobId(text: string): number | null {
  const m = String(text).match(/任务：#(\d+)/)
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) ? n : null
}

/** 配音已产出或流水线已过配音阶段时，不再展示「继续生成音频」 */
function shouldShowTimelineGenerateAudioButton(item: ChatMessage): boolean {
  if (!isTimelineStepMessage(item)) return false
  const jid = parseTimelineJobId(item.content)
  if (jid == null) return false
  const st = chatStore.jobStatusMap[jid]
  const d = lastJobDetail.value
  if (d?.job?.id === jid && (d.audios?.length ?? 0) > 0) return false
  const ri = pipelineStageIndex('rendering_video')
  if (st && ri >= 0 && pipelineStageIndex(st) >= ri) return false
  if (st && (st === 'ready_for_review' || st === 'approved')) return false
  return true
}

/** 与按钮一致：避免「下一步将进入配音」与已完成音频矛盾 */
function timelineNextHint(item: ChatMessage): string {
  const base = parseTimelineSummary(item.content).nextStep
  if (shouldShowTimelineGenerateAudioButton(item)) return base
  if (/配音|音频/.test(base)) return '配音已生成，可在下方「音频步骤完成」消息中试听或确认后继续。'
  return base
}

function subtitleDraftFor(item: ChatMessage): string {
  if (subtitleDraftMap.value[item.id] != null) return subtitleDraftMap.value[item.id]
  const lines = parseSubtitlePreviewLines(item.content)
  const fallback = lines.length ? lines.join('\n') : ''
  subtitleDraftMap.value[item.id] = fallback
  return fallback
}

function draftSubtitleCues(item: ChatMessage): { cues: Array<{ start: number; end: number; text: string }>; jid: number } | null {
  const jid = parseSubtitleJobId(item.content) ?? chatStore.activeJobId
  if (!jid) return null
  const raw = (subtitleDraftMap.value[item.id] || '').trim()
  if (!raw) return null
  const lines = raw.split('\n').map((x) => x.trim()).filter(Boolean)
  if (!lines.length) return null
  const per = 2.5
  const cues = lines.map((text, idx) => ({
    start: Number((idx * per).toFixed(2)),
    end: Number(((idx + 1) * per).toFixed(2)),
    text,
  }))
  return { cues, jid }
}

async function onSaveSubtitles(item: ChatMessage) {
  const built = draftSubtitleCues(item)
  if (!built) {
    ElMessage.warning('未识别到任务 ID 或字幕内容为空')
    return
  }
  await updateLatestSubtitles(built.jid, built.cues)
  chatStore.appendAssistantMessage({
    id: uuidv4(),
    role: 'assistant',
    content: `字幕已保存（任务 #${built.jid}，共 ${built.cues.length} 条）。如确认可发送「确认字幕」继续生成音频。`,
    createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  })
}


onMounted(() => {
  void chatStore.bootstrapTodayCandidates()
})

onBeforeUnmount(() => {
  for (const arr of Object.values(uploadPreviewMap.value)) {
    for (const u of arr) {
      try {
        URL.revokeObjectURL(u.objectUrl)
      } catch {
        /* ignore */
      }
    }
  }
  for (const arr of Object.values(pendingUploadMap.value)) {
    for (const u of arr) {
      try {
        URL.revokeObjectURL(u.objectUrl)
      } catch {
        /* ignore */
      }
    }
  }
  if (previewAudio) {
    previewAudio.pause()
    previewAudio = null
  }
})

function pushPipelineBubble(jobId: number, stageKey: string, body: string) {
  if (CHAT_OWNED_STAGES.has(stageKey)) return
  // “素材候选”消息里已包含“步骤 1/2（正文抽取）”，
  // 若此时再补发正文抽取卡片，视觉上会变成“先素材候选，后正文抽取”。
  if (
    stageKey === 'extracting_content' &&
    chatStore.messages.some((m) => m.role === 'assistant' && m.content.includes('步骤 2/2：素材候选如下'))
  ) {
    return
  }
  const done = new Set(pushedStages.value[jobId] || [])
  if (done.has(stageKey)) return
  const ui = pipelineStageUi(stageKey)
  chatStore.appendAssistantMessage({
    id: uuidv4(),
    role: 'assistant',
    content: body,
    createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    bubbleKind: 'pipeline',
    pipeline: {
      jobId,
      stageKey,
      title: ui.label,
      icon: ui.icon,
      accent: ui.accent,
    },
  })
  done.add(stageKey)
  pushedStages.value[jobId] = Array.from(done)
}

function handleJobDetail(d: JobDetailResponse) {
  try {
    if (!d?.job?.id) return
    lastJobDetail.value = d
    const jid = d.job.id
    const cur = d.job.status

  if (cur === 'failed') {
    const fs = d.job.failed_stage || '未知阶段'
    const errMsg = (d.job.error_message || '').trim()
    const ui = pipelineStageUi('failed')
    chatStore.appendAssistantMessage({
      id: uuidv4(),
      role: 'assistant',
      content: `流水线失败\n阶段：${fs}\n${errMsg || '（无详细错误信息）'}`,
      createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      bubbleKind: 'pipeline',
      pipeline: {
        jobId: jid,
        stageKey: 'failed',
        title: ui.label,
        icon: ui.icon,
        accent: ui.accent,
      },
    })
    lastPollStatus.value[jid] = cur
    return
  }

  const prev = lastPollStatus.value[jid]

  if (prev === undefined) {
    // 首次拿到状态时按阶段顺序补齐，配合 pushedStages 去重可避免重复和错序。
    const i = pipelineStageIndex(cur)
    if (i > 0) {
      for (let k = 0; k < i; k++) {
        const sk = PIPELINE_STAGES[k].key
        if (sk === 'created') continue
        pushPipelineBubble(jid, sk, buildPipelineStageBody(sk, d))
      }
    }
    lastPollStatus.value[jid] = cur
    return
  }

  if (prev === cur) {
    return
  }

  const pi = pipelineStageIndex(cur)
  const pj = pipelineStageIndex(prev)
  if (pi >= 0 && pj >= 0 && pi < pj) {
    lastPollStatus.value[jid] = undefined
    pushedStages.value[jid] = []
    chatStore.appendAssistantMessage({
      id: uuidv4(),
      role: 'assistant',
      content: `任务 #${jid} 已重新进入流水线，将分步推送进度。`,
      createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    })
    handleJobDetail(d)
    return
  }

  if (pi >= 0 && pj >= 0 && pi > pj) {
    // 跨阶段跳转时按区间补齐，避免只补上一阶段导致时序错乱。
    for (let k = pj; k < pi; k++) {
      const sk = PIPELINE_STAGES[k].key
      if (sk === 'created') continue
      pushPipelineBubble(jid, sk, buildPipelineStageBody(sk, d))
    }
  } else if (prev !== 'created') {
    pushPipelineBubble(jid, prev, buildPipelineStageBody(prev, d))
  }
  lastPollStatus.value[jid] = cur
  } catch (e) {
    console.error('handleJobDetail', e)
  }
}

/** 不依赖右侧 Job 面板定时器：重跑 / 多步命令后持续拉详情并驱动对话内流水线卡片 */
async function runJobDetailPollLoop(jobId: number, gen: number) {
  for (let i = 0; i < 120; i++) {
    if (gen !== jobDetailPollGen.value) return
    try {
      const d = await fetchJobDetail(jobId)
      if (gen !== jobDetailPollGen.value) return
      handleJobDetail(d)
    } catch {
      break
    }
    await new Promise((r) => setTimeout(r, 2000))
  }
}

watch(
  () => chatStore.pipelineResumeNonce,
  (n) => {
    if (n < 1) return
    const jid = chatStore.activeJobId
    if (jid == null) return
    jobDetailPollGen.value += 1
    const gen = jobDetailPollGen.value
    void runJobDetailPollLoop(jid, gen)
  },
)

function onStatusChange(payload: { jobId: number; status: string }) {
  chatStore.setJobStatus(payload.jobId, payload.status)
}

function onSwitchJob(id: number | null) {
  chatStore.setActiveJobId(id)
}

async function onStartCandidate(index: number) {
  await chatStore.startFromCandidate(index)
}

async function onQuickSend(text: string) {
  await sendText(text)
}

function appendArtifactToChat(kind: JobArtifactKind) {
  const d = lastJobDetail.value
  const jid = chatStore.activeJobId
  if (d == null || jid == null || d.job.id !== jid) {
    chatStore.appendAssistantMessage({
      id: uuidv4(),
      role: 'assistant',
      content: '【产物查看】请先在右侧选择任务，并点「刷新状态」等详情加载完成后再试。',
      createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    })
    void scrollToBottom()
    return
  }
  chatStore.appendAssistantMessage({
    id: uuidv4(),
    role: 'assistant',
    content: formatJobArtifact(kind, d),
    createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    bubbleKind: 'code',
  })
  void scrollToBottom()
}

function onPickAssets(messageId?: string) {
  activeAssetCardId.value = messageId || null
  uploadRef.value?.click()
}

function onPickAssetsFromComposer() {
  onPickAssets()
}

async function onAssetsChange(e: Event) {
  const el = e.target as HTMLInputElement
  const files = Array.from(el.files || [])
  if (!files.length) return
  const previews = files.map((f) => ({
    id: uuidv4(),
    name: f.name,
    objectUrl: URL.createObjectURL(f),
    media: (f.type.startsWith('video') ? 'video' : 'image') as 'image' | 'video',
    file: f,
  }))
  const cardId = activeAssetCardId.value
  if (cardId) {
    // 素材卡片上传改为“先选再用”：上传后先放到卡片待选区，不立即参与流程。
    pendingUploadMap.value[cardId] = [...(pendingUploadMap.value[cardId] || []), ...previews]
    pickedUploadMap.value[cardId] = pickedUploadMap.value[cardId] || []
  } else {
    await chatStore.uploadAssets(files, {
      previews: previews.map(({ name, objectUrl, media }) => ({ name, objectUrl, media })),
      silentMessage: false,
    })
  }
  el.value = ''
  activeAssetCardId.value = null
}

function toggleUploadedPick(messageId: string, uploadId: string) {
  const cur = new Set(pickedUploadMap.value[messageId] || [])
  if (cur.has(uploadId)) cur.delete(uploadId)
  else cur.add(uploadId)
  pickedUploadMap.value[messageId] = Array.from(cur)
}

function bumpRerenderDuration(delta: number) {
  rerenderForm.duration_sec = Math.min(22, Math.max(12, Math.round(Number(rerenderForm.duration_sec) + delta)))
}

async function onRerenderWithConfig() {
  await chatStore.rerenderActiveJob({
    duration_sec: rerenderForm.duration_sec,
    subtitle_tone: rerenderForm.subtitle_tone,
    tts_voice: rerenderForm.tts_voice,
    aspect_ratio: rerenderForm.aspect_ratio,
    must_use_uploaded_assets: rerenderForm.must_use_uploaded_assets,
    prefer_video_assets: rerenderForm.prefer_video_assets,
  })
}

async function onInputEnter(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.shiftKey) return
  e.preventDefault()
  await onSend()
}

function pushLocalUserMessage(text: string) {
  chatStore.messages.push({
    id: uuidv4(),
    role: 'user',
    content: text,
    createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  })
}

function splitSubtitleLines(raw: string): string[] {
  const t = raw.trim()
  if (!t) return []
  const byLine = t
    .split('\n')
    .map((x) => x.trim())
    .filter(Boolean)
  if (byLine.length > 1) return byLine
  return t
    .split(/[。！？!?；;]+/)
    .map((x) => x.trim())
    .filter(Boolean)
}

async function handleSlashCommand(text: string): Promise<boolean> {
  // /sucai：在对话中加载素材候选卡片，支持勾选与上传，确认在卡片内完成
  if (/^\/sucai(\s|$)/i.test(text)) {
    pushLocalUserMessage(text)
    const jid = chatStore.activeJobId
    if (!jid) {
      chatStore.appendAssistantMessage({
        id: uuidv4(),
        role: 'assistant',
        content: '当前没有活动任务，请先「一键渲染」或选择任务后再用 /sucai。',
        createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      })
      return true
    }
    chatStore.loading = true
    try {
      const res = await postCommand('素材候选', jid)
      if (res.active_job_id != null) chatStore.activeJobId = res.active_job_id
      if (Array.isArray(res.candidates)) {
        chatStore.lastCandidates = res.candidates.length > 0 ? res.candidates : null
      }
      chatStore.appendAssistantReply(res.reply)
      chatStore.appendAssistantMessage({
        id: uuidv4(),
        role: 'assistant',
        content:
          '已在上方展示素材候选卡片：可勾选远程素材、点卡片内「本地上传」添加文件；完成后点「确认所选素材并继续」。',
        createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      })
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '加载素材候选失败')
    } finally {
      chatStore.loading = false
    }
    onPickAssets()
    void scrollToBottom()
    return true
  }

  // /zimu：在对话中插入可编辑字幕气泡（默认带上一条已保存字幕；也可在命令后接草稿）
  const zm = text.match(/^\/zimu(?:\s+([\s\S]+))?$/i)
  if (zm) {
    pushLocalUserMessage(text)
    const jid = chatStore.activeJobId
    if (!jid) {
      chatStore.appendAssistantMessage({
        id: uuidv4(),
        role: 'assistant',
        content: '当前没有活动任务，先执行「做第 N 条」或选择任务后再用 /zimu。',
        createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      })
      return true
    }
    let lines = splitSubtitleLines((zm[1] || '').trim())
    if (!lines.length) {
      try {
        const d = await fetchJobDetail(jid)
        lines = (d.subtitle_cues || [])
          .map((c) => String(c.text || '').trim())
          .filter(Boolean)
      } catch (e) {
        ElMessage.error(e instanceof Error ? e.message : '拉取任务详情失败')
        return true
      }
    }
    if (!lines.length) {
      for (let i = chatStore.messages.length - 1; i >= 0; i--) {
        const m = chatStore.messages[i]
        if (!isSubtitleStepMessage(m)) continue
        const mj = parseSubtitleJobId(m.content)
        if (mj != null && mj !== jid) continue
        const fromChat = parseSubtitlePreviewLines(m.content)
        if (fromChat.length) {
          lines = fromChat
          break
        }
      }
    }
    if (!lines.length) {
      chatStore.appendAssistantMessage({
        id: uuidv4(),
        role: 'assistant',
        content: '当前任务还没有已保存的字幕。请先发送「生成字幕」，或用「/zimu 第一句。第二句」先写入草稿。',
        createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      })
      return true
    }
    const msg: ChatMessage = {
      id: uuidv4(),
      role: 'assistant',
      content: buildSubtitleStepReply(jid, lines),
      createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    }
    subtitleDraftMap.value[msg.id] = lines.join('\n')
    chatStore.appendAssistantMessage(msg)
    void scrollToBottom()
    return true
  }

  return false
}

function tierClass(tier: string) {
  if (tier === '可做') return 'tier-ok'
  if (tier === '不建议') return 'tier-bad'
  return 'tier-mid'
}
</script>

<template>
  <main class="page">
    <div class="wrap">
      <header class="top">
        <div>
          <h1 class="title">海外新闻视频工厂</h1>
        </div>
        <div class="actions">
          <el-button @click="flowCardMode = flowCardMode === 'compact' ? 'detail' : 'compact'">
            卡片：{{ flowCardMode === 'compact' ? '简洁' : '详细' }}
          </el-button>
          <el-button class="btn-green" type="primary" :loading="chatStore.ingestLoading" @click="chatStore.runIngest()">
            抓取新闻
          </el-button>
          <el-button :disabled="chatStore.messages.length === 0" @click="onClear">清空会话</el-button>
        </div>
      </header>

      <section class="shell">
        <aside class="left-panel">
          <div class="candidates">
            <div class="section-bar">
              <div class="section-head">
                <span class="section-title">候选</span>
                <span class="section-sub">美东当日热点优先 · 点击一键渲染</span>
              </div>
              <el-button
                size="small"
                class="translate-btn"
                :loading="chatStore.translatingCandidates"
                :disabled="!chatStore.lastCandidates?.length"
                @click="chatStore.translateCurrentCandidatesToZh()"
              >
                转为中文
              </el-button>
            </div>
            <div v-if="chatStore.lastCandidates?.length" class="candidate-list">
              <div v-for="c in chatStore.lastCandidates" :key="c.index" class="candidate-card">
                <div class="candidate-top">
                  <span class="idx">{{ c.index }}</span>
                  <a class="candidate-title" :href="c.url" target="_blank" rel="noreferrer" :title="c.title_zh || c.title">
                    {{ c.title_zh || c.title }}
                  </a>
                </div>
                <div v-if="c.title_zh && c.title_zh !== c.title" class="src">原文：{{ c.title }}</div>
                <div v-if="c.summary_zh || c.summary" class="src">{{ c.summary_zh || c.summary }}</div>
                <div class="candidate-actions">
                  <span class="src">
                    {{ c.source }}
                    <span v-if="c.heat_index != null"> · 🔥{{ c.heat_index }}</span>
                    <span v-else-if="c.score_10 != null"> · 🔥{{ c.score_10 }}</span>
                  </span>
                  <div class="candidate-actions-right">
                    <span class="tier" :class="tierClass(c.tier)">{{ c.tier }}</span>
                    <button type="button" class="start-btn" :disabled="chatStore.loading" @click="onStartCandidate(c.index)">
                      一键渲染
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="candidate-empty">先点上方“抓取新闻”，再查看候选。</div>
          </div>
        </aside>

        <section class="right-panel">
          <div class="rerender-panel card">
            <div class="rerender-bar">
              <span class="rerender-bar-title">配置</span>
              <div class="rerender-items">
                <div class="rerender-item">
                  <label class="rerender-lbl" for="rerender-duration" title="视频时长，12～22 秒">时长</label>
                  <div id="rerender-duration" class="rerender-stepper" title="范围 12～22 秒">
                    <button
                      type="button"
                      class="step-btn"
                      aria-label="减 1 秒"
                      :disabled="rerenderForm.duration_sec <= 12 || chatStore.loading"
                      @click="bumpRerenderDuration(-1)"
                    >
                      −
                    </button>
                    <span class="step-value">{{ rerenderForm.duration_sec }}</span>
                    <button
                      type="button"
                      class="step-btn"
                      aria-label="加 1 秒"
                      :disabled="rerenderForm.duration_sec >= 22 || chatStore.loading"
                      @click="bumpRerenderDuration(1)"
                    >
                      +
                    </button>
                  </div>
                </div>
                <div class="rerender-item">
                  <label class="rerender-lbl" for="rerender-sub">字幕</label>
                  <el-select
                    id="rerender-sub"
                    v-model="rerenderForm.subtitle_tone"
                    size="small"
                    class="rerender-select"
                    fit-input-width
                    popper-class="rerender-select-dropdown rerender-subtitle-dropdown"
                  >
                    <el-option label="精简字幕" value="brief" />
                    <el-option label="标准字幕" value="normal" />
                  </el-select>
                </div>
                <div class="rerender-item">
                  <label class="rerender-lbl" for="rerender-voice">音色</label>
                  <el-select
                    id="rerender-voice"
                    v-model="rerenderForm.tts_voice"
                    size="small"
                    class="rerender-select rerender-select-voice"
                    :loading="previewingVoice"
                    filterable
                    allow-create
                    default-first-option
                    reserve-keyword
                    placeholder="搜索或输入 voice"
                    popper-class="rerender-select-dropdown rerender-voice-dropdown"
                  >
                    <el-option v-for="v in TTS_VOICE_OPTIONS" :key="v.value" :label="v.label" :value="v.value" />
                  </el-select>
                </div>
                <div class="rerender-item">
                  <label class="rerender-lbl" for="rerender-ratio">比例</label>
                  <el-select
                    id="rerender-ratio"
                    v-model="rerenderForm.aspect_ratio"
                    size="small"
                    class="rerender-select"
                    fit-input-width
                    popper-class="rerender-select-dropdown rerender-subtitle-dropdown"
                  >
                    <el-option label="竖屏 9:16" value="9:16" />
                    <el-option label="横屏 16:9" value="16:9" />
                  </el-select>
                </div>
                <div class="rerender-item rerender-item-switch">
                  <span class="rerender-lbl rerender-lbl-wide" title="开启后仅使用已上传素材">仅已上传</span>
                  <el-switch v-model="rerenderForm.must_use_uploaded_assets" size="small" />
                </div>
                <div class="rerender-item rerender-item-switch">
                  <span class="rerender-lbl rerender-lbl-wide" title="关闭则优先静态图">优先视频</span>
                  <el-switch v-model="rerenderForm.prefer_video_assets" size="small" />
                </div>
              </div>
              <el-button
                class="btn-green rerender-submit-btn"
                type="primary"
                size="small"
                :disabled="chatStore.activeJobId == null || chatStore.loading"
                @click="onRerenderWithConfig"
              >
                应用并重跑
              </el-button>
            </div>
          </div>

          <div ref="listRef" class="chat">
            <div v-if="chatStore.messages.length === 0" class="empty">
              <p class="empty-title">还没有消息</p>
              <p class="empty-desc">先抓取候选，再点左侧某条“一键开始渲染”。</p>
            </div>
            <template v-for="item in chatStore.messages" :key="item.id">
              <div class="row" :class="item.role === 'user' ? 'row-user' : 'row-bot'">
                <div v-if="item.role !== 'user'" class="avatar bot">{{ AGENT_NAME.slice(0, 1) }}</div>
                <div class="bubble-block">
                  <div class="meta">
                    {{ item.role === 'user' ? '你' : AGENT_NAME }} · {{ item.createdAt }}
                  </div>
                  <div
                    v-if="item.bubbleKind === 'pipeline' && item.pipeline"
                    class="bubble pipeline-bubble"
                    :data-accent="item.pipeline.accent"
                  >
                    <div class="pipe-head">
                      <span class="pipe-icon" aria-hidden="true">{{ item.pipeline.icon }}</span>
                      <div class="pipe-head-text">
                        <div class="pipe-kicker">任务 #{{ item.pipeline.jobId }}</div>
                        <div class="pipe-title">{{ item.pipeline.title }}</div>
                      </div>
                    </div>
                    <pre class="pipe-body">{{ item.content }}</pre>
                  </div>
                  <div
                    v-else-if="item.role === 'assistant' && item.bubbleKind === 'code'"
                    class="bubble is-bot artifact-bubble"
                  >
                    <pre class="artifact-pre">{{ item.content }}</pre>
                  </div>
                  <div v-else-if="isUploadPreviewMessage(item)" class="bubble is-bot upload-preview-bubble">
                    <p class="text upload-preview-text">{{ item.content }}</p>
                    <div class="upload-preview-grid">
                      <div
                        v-for="(u, ui) in item.uploadPreviews"
                        :key="`${item.id}-up-${ui}`"
                        class="upload-preview-card"
                      >
                        <span class="upload-preview-name" :title="u.name">{{ u.name }}</span>
                        <img v-if="u.media === 'image'" class="asset-preview-image" :src="u.objectUrl" alt="" />
                        <video v-else class="asset-preview-video" :src="u.objectUrl" controls preload="metadata" />
                      </div>
                    </div>
                  </div>
                  <div v-else-if="isFlowCardMessage(item)" class="bubble is-bot flow-card-bubble">
                    <div class="flow-card-head">
                      <div class="flow-card-title">
                        <span class="flow-icon">{{ flowIconByTag(parseFlowCard(item.content)?.desc) }}</span>
                        <span>{{ parseFlowCard(item.content)?.title }}</span>
                      </div>
                    </div>
                    <ul v-if="parseFlowCard(item.content)?.items?.length" class="flow-card-list">
                      <li
                        v-for="(line, fi) in displayLines(parseFlowCard(item.content)?.items || [], 3)"
                        :key="`${item.id}-f-${fi}`"
                      >
                        {{ line }}
                      </li>
                    </ul>
                  </div>
                  <div
                    v-else-if="isAssetCandidateMessage(item)"
                    class="bubble is-bot asset-candidates-bubble"
                  >
                    <div class="asset-candidates-title-row">
                      <div class="asset-candidates-title"><span class="flow-icon">🖼️</span>素材候选（请选择要使用的素材）</div>
                      <button class="asset-submit-btn ghost" type="button" :disabled="chatStore.loading" @click="onPickAssets(item.id)">
                        本地上传
                      </button>
                      <button class="asset-submit-btn ghost" type="button" @click="toggleAllAssets(item.id, item.content)">
                        {{
                          (
                            (parseAssetCandidates(item.content).length === 0 ||
                              parseAssetCandidates(item.content).every((x) => (pickedAssetMap[item.id] || []).includes(x.index))) &&
                            ((pendingUploadMap[item.id] || []).length === 0 ||
                              (pendingUploadMap[item.id] || []).every((u) => (pickedUploadMap[item.id] || []).includes(u.id)))
                          )
                            ? '取消全选'
                            : '一键全选'
                        }}
                      </button>
                    </div>
                    <div class="asset-candidates-list">
                      <div
                        v-for="cand in parseAssetCandidates(item.content)"
                        :key="`${cand.index}-${cand.url}`"
                        class="asset-candidate-card"
                        :class="(pickedAssetMap[item.id] || []).includes(cand.index) ? 'is-selected' : ''"
                        @click="toggleAssetPick(item.id, cand.index)"
                      >
                        <span class="asset-candidate-badge">#{{ cand.index }}</span>
                        <span class="asset-type-chip">{{ cand.type === 'video' ? '视频' : '图片' }}</span>
                        <img v-if="cand.type === 'image'" class="asset-preview-image" :src="cand.url" alt="候选素材预览" />
                        <video v-else class="asset-preview-video" :src="cand.url" controls preload="metadata" />
                      </div>
                    </div>
                    <div v-if="pendingUploadMap[item.id]?.length" class="upload-preview-inline">
                      <div class="upload-preview-inline-title">已上传附件（请勾选后使用）</div>
                      <div class="upload-preview-grid">
                        <div
                          v-for="u in pendingUploadMap[item.id]"
                          :key="u.id"
                          class="upload-preview-card selectable-upload"
                          :class="(pickedUploadMap[item.id] || []).includes(u.id) ? 'is-selected' : ''"
                          @click="toggleUploadedPick(item.id, u.id)"
                        >
                          <span class="upload-preview-name" :title="u.name">{{ u.name }}</span>
                          <img v-if="u.media === 'image'" class="asset-preview-image" :src="u.objectUrl" alt="" />
                          <video v-else class="asset-preview-video" :src="u.objectUrl" controls preload="metadata" />
                        </div>
                      </div>
                    </div>
                    <div v-if="uploadPreviewMap[item.id]?.length" class="upload-preview-inline">
                      <div class="upload-preview-inline-title">已确认使用的上传素材</div>
                      <div class="upload-preview-grid">
                        <div
                          v-for="(u, ui) in uploadPreviewMap[item.id]"
                          :key="`${item.id}-inline-up-${ui}`"
                          class="upload-preview-card"
                        >
                          <span class="upload-preview-name" :title="u.name">{{ u.name }}</span>
                          <img v-if="u.media === 'image'" class="asset-preview-image" :src="u.objectUrl" alt="" />
                          <video v-else class="asset-preview-video" :src="u.objectUrl" controls preload="metadata" />
                        </div>
                      </div>
                    </div>
                    <div class="asset-candidate-actions">
                      <button class="asset-submit-btn primary-regen" type="button" :disabled="chatStore.loading" @click="submitPickedAssets(item.id)">
                        确认所选素材并继续
                      </button>
                      <button
                        class="asset-submit-btn ghost"
                        type="button"
                        :disabled="!(pickedAssetMap[item.id] || []).length"
                        @click="onPickAssetByIndex((pickedAssetMap[item.id] || [])[0])"
                      >
                        仅选第一项快速继续
                      </button>
                    </div>
                  </div>
                  <div v-else-if="isSubtitleStepMessage(item)" class="bubble is-bot subtitle-editor-bubble">
                    <div class="flow-card-head">
                      <div class="flow-card-title"><span class="flow-icon">📝</span>{{ firstLine(item.content) }}</div>
                    </div>
                    <ul v-if="parseSubtitlePreviewLines(item.content).length" class="flow-card-list">
                      <li
                        v-for="(line, si) in displayLines(parseSubtitlePreviewLines(item.content), 4)"
                        :key="`${item.id}-s-${si}`"
                      >
                        {{ line }}
                      </li>
                    </ul>
                    <textarea
                      class="subtitle-editor"
                      :value="subtitleDraftFor(item)"
                      placeholder="每行一条字幕，可直接修改"
                      @input="subtitleDraftMap[item.id] = ($event.target as HTMLTextAreaElement).value"
                    />
                    <div class="subtitle-actions">
                      <button class="asset-submit-btn" type="button" @click="onSaveSubtitles(item)">保存字幕</button>
                      <button class="asset-submit-btn ghost" type="button" @click="onQuickSend('确认字幕')">确认字幕并生成音频</button>
                    </div>
                  </div>
                  <div v-else-if="isAudioStepMessage(item)" class="bubble is-bot media-result-bubble">
                    <div class="flow-card-head">
                      <div class="flow-card-title"><span class="flow-icon">🎧</span>{{ firstLine(item.content) }}</div>
                    </div>
                    <ul v-if="compactBodyLines(item.content).length" class="flow-card-list">
                      <li
                        v-for="(line, ai) in displayLines(compactBodyLines(item.content), 4)"
                        :key="`${item.id}-a-${ai}`"
                      >
                        {{ line }}
                      </li>
                    </ul>
                    <audio v-if="parseAudioPreviewUrl(item.content)" class="inline-audio" :src="parseAudioPreviewUrl(item.content)" controls />
                    <div class="subtitle-actions">
                      <button class="asset-submit-btn ghost" type="button" @click="onQuickSend('确认音频')">确认音频并合成视频</button>
                    </div>
                  </div>
                  <div v-else-if="isTimelineStepMessage(item)" class="bubble is-bot media-result-bubble timeline-result-bubble">
                    <div class="timeline-head">
                      <div class="timeline-title"><span class="flow-icon">⏱️</span>{{ parseTimelineSummary(item.content).title }}</div>
                      <div v-if="parseTimelineSummary(item.content).countText" class="timeline-count">
                        {{ parseTimelineSummary(item.content).countText }}
                      </div>
                    </div>
                    <div v-if="parseTimelineSummary(item.content).previews.length" class="timeline-preview-wrap">
                      <div class="timeline-preview-title">字幕预览</div>
                      <ol class="timeline-preview-list">
                        <li
                          v-for="(line, li) in displayLines(parseTimelineSummary(item.content).previews, 3)"
                          :key="`${item.id}-pv-${li}`"
                        >
                          {{ line }}
                        </li>
                      </ol>
                    </div>
                    <div class="timeline-next">{{ timelineNextHint(item) }}</div>
                    <div v-if="shouldShowTimelineGenerateAudioButton(item)" class="subtitle-actions">
                      <button class="asset-submit-btn ghost" type="button" @click="onQuickSend('生成音频')">继续生成音频</button>
                    </div>
                  </div>
                  <div v-else-if="isVideoStepMessage(item)" class="bubble is-bot media-result-bubble">
                    <div class="flow-card-head">
                      <div class="flow-card-title"><span class="flow-icon">🎬</span>{{ firstLine(item.content) }}</div>
                    </div>
                    <ul v-if="compactBodyLines(item.content).length" class="flow-card-list">
                      <li
                        v-for="(line, vi) in displayLines(compactBodyLines(item.content), 4)"
                        :key="`${item.id}-v-${vi}`"
                      >
                        {{ line }}
                      </li>
                    </ul>
                    <video
                      v-if="parseVideoPreviewUrl(item.content)"
                      class="inline-video"
                      :src="parseVideoPreviewUrl(item.content)"
                      controls
                      preload="metadata"
                    />
                    <div class="subtitle-actions">
                      <a
                        v-if="parseVideoDownloadUrl(item.content)"
                        class="asset-submit-btn ghost"
                        :href="parseVideoDownloadUrl(item.content)"
                        target="_blank"
                        rel="noreferrer"
                        download
                        >下载视频</a
                      >
                    </div>
                  </div>
                  <div v-else class="bubble" :class="item.role === 'user' ? 'is-user' : 'is-bot'">
                    <p v-if="item.role === 'user'" class="text">{{ item.content }}</p>
                    <p v-else class="text plain-rich">
                      <template v-for="(seg, si) in assistantSegments(item)" :key="si">
                        <span v-if="seg.type === 'index'" class="seg-index">{{ seg.text }}</span>
                        <span v-else-if="seg.type === 'source'" class="seg-source">{{ seg.text }}</span>
                        <a
                          v-else-if="seg.type === 'link'"
                          class="seg-link"
                          :href="seg.text.includes('|||') ? seg.text.split('|||')[1] : seg.text"
                          target="_blank"
                          rel="noreferrer noopener"
                          >{{ seg.text.includes('|||') ? seg.text.split('|||')[0] : seg.text }}</a>
                        <span v-else>{{ seg.text }}</span>
                      </template>
                    </p>
                  </div>
                </div>
                <div v-if="item.role === 'user'" class="avatar user">我</div>
              </div>
            </template>
            <div v-if="chatStore.loading" class="row row-bot row-loading">
              <div class="avatar bot">{{ AGENT_NAME.slice(0, 1) }}</div>
              <div class="bubble-block">
                <div class="meta">{{ AGENT_NAME }} · 正在思考</div>
                <div class="bubble is-bot typing-bubble" aria-live="polite">
                  <span class="typing-dot" />
                  <span class="typing-dot" />
                  <span class="typing-dot" />
                </div>
              </div>
            </div>
          </div>

          <div class="quick">
            <button
              v-for="q in QUICK_COMMANDS"
              :key="q.text"
              type="button"
              class="chip"
              :class="q.kind === 'flow' ? 'chip-flow' : ''"
              @click="onQuickSend(q.text)"
            >
              {{ q.label }}
            </button>
            <template v-if="artifactQuickItems.length">
              <span class="quick-sep" aria-hidden="true" />
              <button
                v-for="a in artifactQuickItems"
                :key="a.kind"
                type="button"
                class="chip chip-artifact"
                :class="`chip-artifact-${a.kind}`"
                :title="`在对话中展示当前任务的${a.label}数据`"
                @click="appendArtifactToChat(a.kind)"
              >
                {{ a.label }}
              </button>
            </template>
          </div>

          <div class="composer">
            <input
              ref="uploadRef"
              type="file"
              class="hidden-upload"
              accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.m4v,.webm"
              multiple
              @change="onAssetsChange"
            />
            <div class="composer-shell">
              <button
                type="button"
                class="icon-upload-btn"
                :disabled="chatStore.loading"
                title="上传素材"
                aria-label="上传素材"
                @click="onPickAssetsFromComposer"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M9 12.5 15.2 6.3a3.2 3.2 0 1 1 4.5 4.5l-8.4 8.4a5 5 0 0 1-7.1-7.1l8.7-8.7"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
              <el-input
                v-model="input"
                class="composer-input"
                type="textarea"
                :rows="3"
                placeholder="支持 /sucai 在对话里选素材与上传，/zimu 在对话里改字幕；也可发 改标题/改简介/改素材 + 需求"
                resize="none"
                @keydown.enter="onInputEnter"
              />
              <div v-if="showSlashPanel" class="slash-panel">
                <button
                  v-for="cmd in slashCommandOptions"
                  :key="cmd.command"
                  type="button"
                  class="slash-item"
                  @click="pickSlashCommand(cmd.template)"
                >
                  <span class="slash-cmd">{{ cmd.command }}</span>
                  <span class="slash-desc">{{ cmd.description }}</span>
                </button>
              </div>
              <div class="composer-actions">
                <button type="button" class="send-icon-btn" :disabled="!canSend" @click="onSend">
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M4 12 20 4 14 20 11 13 4 12Z" fill="currentColor" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </section>

        <aside class="job-panel">
          <div class="job-tools card">
            <div class="job-tools-title">任务切换</div>
            <el-select
              v-model="chatStore.activeJobId"
              class="job-select"
              placeholder="选择任务"
              :disabled="chatStore.jobHistory.length === 0"
              @change="onSwitchJob"
            >
              <el-option
                v-for="id in chatStore.jobHistory"
                :key="id"
                :label="`#${id} · ${chatStore.jobStatusMap[id] || 'created'}`"
                :value="id"
              />
            </el-select>
          </div>
          <JobStatusPanel
            :job-id="chatStore.activeJobId"
            :poll-kick="chatStore.pipelineResumeNonce"
            @status-change="onStatusChange"
            @job-detail="handleJobDetail"
          />
        </aside>
      </section>
    </div>
  </main>
</template>

<style scoped>
.page {
  height: 100vh;
  padding: 14px;
  overflow: hidden;
  background: linear-gradient(180deg, #1b1e23 0%, #14171c 100%);
}

.wrap {
  width: min(1880px, 100%);
  margin: 0 auto;
  height: 100%;
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 12px;
}

.top {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
  background: #2a2f37;
  border: 1px solid #3a404a;
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.title {
  margin: 0;
  font-size: 1.65rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #eceff4;
  line-height: 1.25;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.shell {
  overflow: visible;
  display: grid;
  grid-template-columns: minmax(380px, 440px) minmax(0, 1fr) minmax(360px, 420px);
  min-height: 0;
  gap: 12px;
}

.card {
  border: 1px solid #3a404a;
  border-radius: 14px;
  background: #242931;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.left-panel {
  background: transparent;
  overflow: hidden;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
}

.right-panel {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto auto;
  background: #1e232a;
  overflow: hidden;
}

.rerender-panel {
  --rerender-lbl-w: 48px;
  --rerender-ctl-h: 26px;
  margin: 0;
  padding: 8px 16px;
  border: 1px solid #3e4653;
  border-left: none;
  border-right: none;
  border-radius: 0;
  background: #262c35;
  box-shadow: none;
  overflow-x: auto;
}

.rerender-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.rerender-bar-title {
  font-size: 15px;
  font-weight: 800;
  color: #eef2f8;
  letter-spacing: -0.02em;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  min-height: var(--rerender-ctl-h);
  padding-right: 8px;
}

.rerender-items {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
}

.rerender-item {
  display: inline-grid;
  grid-template-columns: var(--rerender-lbl-w) auto;
  align-items: center;
  align-content: center;
  column-gap: 1px;
  min-width: 0;
}

.rerender-item {
  flex-shrink: 0;
}

.rerender-lbl {
  box-sizing: border-box;
  width: var(--rerender-lbl-w);
  min-height: var(--rerender-ctl-h);
  padding-right: 2px;
  font-size: 13px;
  font-weight: 700;
  line-height: var(--rerender-ctl-h);
  color: #b6beca;
  white-space: nowrap;
  text-align: right;
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.rerender-lbl-wide {
  width: 72px;
}

.rerender-item-switch {
  grid-template-columns: 72px auto;
  column-gap: 3px;
}

.rerender-item-switch .rerender-lbl {
  width: 72px;
}

.rerender-stepper {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-width: 74px;
  height: var(--rerender-ctl-h);
  border-radius: 5px;
  border: 1px solid #4a515d;
  overflow: hidden;
  background: #232830;
}

.step-btn {
  box-sizing: border-box;
  width: 22px;
  height: var(--rerender-ctl-h);
  border: none;
  margin: 0;
  padding: 0;
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
  color: #d0d6df;
  background: #303743;
  cursor: pointer;
  transition: background 0.15s ease;
}

.step-btn:hover:not(:disabled) {
  background: #3a4250;
  color: #f3f5f8;
}

.step-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.step-value {
  box-sizing: border-box;
  min-width: 1.5rem;
  padding: 0 4px;
  text-align: center;
  font-size: 13px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: #e9edf3;
  line-height: var(--rerender-ctl-h);
}

.rerender-select {
  width: 104px;
  min-width: 104px;
  max-width: 104px;
}

.rerender-select-voice {
  width: 160px;
  min-width: 160px;
  max-width: 160px;
}

.rerender-submit-btn {
  flex-shrink: 0;
  font-weight: 800;
  align-self: center;
  margin-left: 4px;
}


.rerender-item :deep(.el-switch) {
  display: inline-flex;
  align-items: center;
}

.rerender-panel :deep(.rerender-select .el-select__wrapper) {
  box-sizing: border-box;
  height: var(--rerender-ctl-h);
  min-height: var(--rerender-ctl-h);
  padding: 0 6px;
  font-size: 13px;
  line-height: var(--rerender-ctl-h);
  border-radius: 5px;
  background: #232830;
  box-shadow: 0 0 0 1px #4a515d inset;
  transition: box-shadow 0.15s ease, background 0.15s ease;
}

.rerender-panel :deep(.rerender-select .el-select__wrapper:hover:not(.is-disabled)) {
  box-shadow: 0 0 0 1px #666f7d inset;
}

.rerender-panel :deep(.rerender-select .el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px #707b8b inset;
}

.rerender-panel :deep(.rerender-select .el-select__selected-item) {
  color: #e5eaf2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.rerender-panel :deep(.rerender-select .el-select__caret) {
  color: #a4adba;
  font-size: 12px;
}

.job-panel {
  border: 1px solid #3a404a;
  border-radius: 14px;
  min-height: 0;
  overflow: hidden;
  overflow-x: hidden;
  background: #262a31;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.job-tools {
  margin: 8px 10px 6px;
  padding: 8px 10px;
}

.job-tools-title {
  font-size: 12px;
  font-weight: 700;
  color: #c8d0db;
  margin-bottom: 8px;
}

.job-select {
  width: 100%;
}

.job-select :deep(.el-input__wrapper) {
  background: #232830;
  border: 1px solid #4a515d;
  box-shadow: none;
}

.job-select :deep(.el-input__inner) {
  color: #e5eaf2;
}

.candidates {
  padding: 12px 12px 8px;
  background: transparent;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.section-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.section-head {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.translate-btn {
  --el-button-bg-color: #3a4453;
  --el-button-border-color: #556172;
  --el-button-text-color: #e7edf6;
  --el-button-hover-bg-color: #465265;
  --el-button-hover-border-color: #6a7890;
  --el-button-active-bg-color: #333d4b;
  --el-button-active-border-color: #4f5a6b;
  --el-button-disabled-bg-color: #2d343f;
  --el-button-disabled-border-color: #434d5b;
  --el-button-disabled-text-color: #7d8796;
  border-radius: 999px;
  padding-inline: 12px;
  font-weight: 700;
  letter-spacing: 0.01em;
  box-shadow: 0 6px 14px rgba(0, 0, 0, 0.22);
}

.section-title {
  font-size: 13px;
  font-weight: 800;
  color: #e5eaf2;
}

.section-sub {
  font-size: 12px;
  color: #9fa8b6;
}

.candidate-list {
  display: grid;
  gap: 8px;
  overflow-y: auto;
  padding-right: 4px;
}

.candidate-list::-webkit-scrollbar {
  width: 8px;
}

.candidate-list::-webkit-scrollbar-thumb {
  background: #c9d1d9;
  border-radius: 99px;
}

.candidate-card {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #3f4652;
  background: #2f3540;
  display: grid;
  gap: 8px;
}

.candidate-top {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 8px;
}

.idx {
  flex-shrink: 0;
  min-width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 800;
  color: #ffffff;
  background: #5a6472;
  border-radius: 6px;
}

.candidate-title {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.45;
  color: #e7ecf3;
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.candidate-title:hover {
  color: #f0f3f8;
  text-decoration: underline;
}

.candidate-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.candidate-actions-right {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.source-link {
  font-size: 12px;
  color: #aeb7c4;
  text-decoration: none;
}

.source-link:hover {
  text-decoration: underline;
}

.start-btn {
  border: 1px solid #616b79;
  color: #ecfdf5;
  background: linear-gradient(135deg, #4d5664, #3e4653);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 11px;
  line-height: 1.35;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 6px 14px rgba(0, 0, 0, 0.28);
  transition: all 0.18s ease;
}

.start-btn:hover {
  background: linear-gradient(135deg, #626d7c, #515b69);
  transform: translateY(-1px);
}

.src {
  color: #a7b0bd;
  font-size: 12px;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tier {
  flex-shrink: 0;
  font-weight: 700;
  padding: 1px 7px;
  font-size: 11px;
  line-height: 1.35;
  border-radius: 999px;
}

.tier-ok {
  color: #d9e0ea;
  background: #3a4250;
}

.tier-mid {
  color: #d9e0ea;
  background: #3a4250;
}

.tier-bad {
  color: #d9e0ea;
  background: #3a4250;
}

.chat {
  min-height: 0;
  overflow-y: auto;
  padding: 14px 16px 8px;
  border-top: 1px solid #39404a;
  background: #1f242c;
}

.chat::-webkit-scrollbar {
  width: 10px;
}

.chat::-webkit-scrollbar-thumb {
  background: #c9d1d9;
  border-radius: 99px;
}

.empty {
  text-align: center;
  padding: 48px 16px;
  border: 1px dashed #4b5360;
  border-radius: 10px;
  background: #2f3641;
}

.empty-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: #e8ecf3;
}

.empty-desc {
  margin: 0;
  font-size: 14px;
  color: #9aa4b3;
}

.row {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 14px;
}

.row-user {
  justify-content: flex-end;
}

.row-bot {
  justify-content: flex-start;
}

.row-loading .bubble-block {
  min-width: 90px;
}

.avatar {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 800;
  border: 1px solid #4a515d;
}

.avatar.bot {
  color: #d8dee8;
  background: #303743;
}

.avatar.user {
  color: #e5eaf2;
  background: #2f353f;
}

.bubble-block {
  max-width: min(560px, 88%);
}

.row-user .bubble-block {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.meta {
  font-size: 11px;
  color: #9099a8;
  margin-bottom: 4px;
}

.bubble {
  padding: 12px 14px;
  border-radius: 12px;
  line-height: 1.55;
  font-size: 14px;
}

.bubble.is-user {
  color: #ffffff;
  background: linear-gradient(135deg, #4d5664, #3e4653);
  border: 1px solid #5b6574;
  border-bottom-right-radius: 4px;
}

.bubble.is-bot {
  color: #e5eaf2;
  background: #313844;
  border: 1px solid #414a57;
  border-bottom-left-radius: 4px;
}

.typing-bubble {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 40px;
  padding: 11px 14px;
  background: linear-gradient(180deg, #323845, #2a303b);
}

.typing-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #d0d6df;
  opacity: 0.35;
  animation: typingPulse 1.2s ease-in-out infinite;
}

.typing-dot:nth-child(2) {
  animation-delay: 0.15s;
}

.typing-dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes typingPulse {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.35;
  }
  40% {
    transform: translateY(-3px);
    opacity: 1;
  }
}

.text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.plain-rich {
  white-space: pre-wrap;
}

.seg-index {
  color: #c6cfdb;
  font-weight: 700;
}

.seg-source {
  color: #aeb7c4;
  font-weight: 600;
}

.seg-link {
  color: inherit;
  font-weight: inherit;
  text-decoration: none;
  word-break: break-all;
  cursor: pointer;
}

.seg-link:hover {
  color: inherit;
}

.flow-card-bubble {
  width: min(720px, 100%);
  border-radius: 14px;
  border: 1px solid rgba(125, 211, 252, 0.34);
  background: linear-gradient(165deg, #283446 0%, #202a39 100%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.flow-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.flow-card-title {
  font-size: 14px;
  font-weight: 800;
  color: #eef6ff;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.flow-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  font-size: 14px;
  line-height: 1;
}

.flow-card-tag {
  font-size: 11px;
  color: #cfe5ff;
  border: 1px solid rgba(96, 165, 250, 0.55);
  background: rgba(15, 23, 42, 0.45);
  border-radius: 4px;
  padding: 2px 8px;
  letter-spacing: 0.04em;
  line-height: 1;
}

.flow-card-tag-solid {
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.75), rgba(59, 130, 246, 0.75));
  border-color: rgba(147, 197, 253, 0.72);
  color: #f8fbff;
}

.flow-card-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 5px;
  color: #e2e8f0;
  font-size: 13px;
  line-height: 1.45;
}

.asset-candidates-bubble {
  width: min(760px, 100%);
  border-radius: 14px;
  border: 1px solid #4a5261;
  background: linear-gradient(180deg, #323a47 0%, #2a313d 100%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.asset-candidates-title {
  font-size: 14px;
  font-weight: 800;
  margin-bottom: 10px;
  color: #f1f5f9;
  letter-spacing: 0.01em;
}

.asset-candidates-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.asset-candidates-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.asset-candidate-card {
  border: 1px solid #505a6b;
  border-radius: 12px;
  padding: 6px;
  background: linear-gradient(180deg, #2f3744 0%, #29313c 100%);
  transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
  cursor: pointer;
  position: relative;
}

.asset-candidate-card:has(.asset-preview-image) {
  border-color: rgba(56, 189, 248, 0.45);
}

.asset-candidate-card:has(.asset-preview-video) {
  border-color: rgba(167, 139, 250, 0.5);
}

.asset-candidate-card:hover {
  transform: translateY(-1px);
  border-color: #64748b;
  box-shadow: 0 10px 18px rgba(0, 0, 0, 0.24);
}

.asset-candidate-card.is-selected {
  border-color: #8b5cf6;
  box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.35), 0 10px 18px rgba(0, 0, 0, 0.24);
}

.asset-candidate-badge {
  position: absolute;
  left: 8px;
  top: 8px;
  z-index: 2;
  font-size: 11px;
  color: #f8fafc;
  font-weight: 700;
  background: rgba(15, 23, 42, 0.75);
  border: 1px solid rgba(148, 163, 184, 0.5);
  border-radius: 999px;
  padding: 1px 7px;
}

.asset-type-chip {
  position: absolute;
  right: 8px;
  top: 8px;
  z-index: 2;
  font-size: 11px;
  color: #e2e8f0;
  background: rgba(30, 41, 59, 0.65);
  border-radius: 999px;
  padding: 1px 7px;
}

.asset-preview-image,
.asset-preview-video {
  width: 100%;
  height: 128px;
  border-radius: 8px;
  object-fit: cover;
  background: #000;
}

.asset-preview-image {
  box-shadow: inset 0 0 0 1px rgba(56, 189, 248, 0.25);
}

.asset-preview-video {
  box-shadow: inset 0 0 0 1px rgba(167, 139, 250, 0.3);
}

.upload-preview-bubble {
  width: min(680px, 100%);
  border-radius: 14px;
  border: 1px solid #4a5261;
  background: linear-gradient(180deg, #313947 0%, #2a313d 100%);
  padding: 12px 14px 14px;
}

.upload-preview-text {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.55;
  color: #e2e8f0;
}

.upload-preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}

.upload-preview-inline {
  margin-top: 12px;
  border-top: 1px solid rgba(148, 163, 184, 0.28);
  padding-top: 10px;
}

.upload-preview-inline-title {
  margin-bottom: 8px;
  font-size: 12px;
  color: #cbd5e1;
  font-weight: 700;
}

.upload-preview-card {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: #0f172a;
}

.selectable-upload {
  cursor: pointer;
}

.selectable-upload.is-selected {
  border-color: #8b5cf6;
  box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.35), 0 10px 18px rgba(0, 0, 0, 0.24);
}

.upload-preview-name {
  display: block;
  font-size: 11px;
  color: #94a3b8;
  padding: 6px 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-bottom: 1px solid rgba(51, 65, 85, 0.6);
}

.asset-candidate-actions {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.asset-submit-btn {
  border: 1px solid #64748b;
  background: linear-gradient(135deg, #5b3a78, #4b3560);
  color: #f8fafc;
  border-radius: 999px;
  padding: 5px 13px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.asset-submit-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: #8b5cf6;
}

.asset-submit-btn.ghost {
  background: #33404d;
  color: #e2e8f0;
}

.asset-submit-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
}

.subtitle-editor-bubble,
.media-result-bubble {
  width: min(680px, 100%);
  border-radius: 14px;
  border: 1px solid #4a5261;
  background: linear-gradient(180deg, #313947 0%, #2a313d 100%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.timeline-result-bubble {
  display: grid;
  gap: 10px;
}

.timeline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.timeline-title {
  font-size: 14px;
  font-weight: 800;
  color: #eff6ff;
}

.timeline-count {
  font-size: 12px;
  color: #dbeafe;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid rgba(96, 165, 250, 0.45);
  background: rgba(37, 99, 235, 0.2);
}

.timeline-preview-wrap {
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: 10px;
  padding: 8px 10px;
  background: rgba(15, 23, 42, 0.26);
}

.timeline-preview-title {
  font-size: 12px;
  font-weight: 700;
  color: #cbd5e1;
  margin-bottom: 5px;
}

.timeline-preview-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 5px;
  color: #e2e8f0;
  font-size: 13px;
  line-height: 1.45;
}

.timeline-next {
  font-size: 12px;
  color: #c7f9cc;
  border-left: 3px solid rgba(74, 222, 128, 0.75);
  padding-left: 8px;
}

.subtitle-editor-bubble .text,
.media-result-bubble .text {
  color: #e8edf6;
}

.subtitle-editor {
  width: 100%;
  min-height: 120px;
  margin-top: 8px;
  border-radius: 8px;
  border: 1px solid #4a515d;
  background: #1f2530;
  color: #edf2f7;
  padding: 10px;
  resize: vertical;
  line-height: 1.6;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02);
  scrollbar-width: thin;
  scrollbar-color: rgba(148, 163, 184, 0.6) rgba(15, 23, 42, 0.2);
}

.subtitle-editor::-webkit-scrollbar {
  width: 8px;
}

.subtitle-editor::-webkit-scrollbar-thumb {
  border-radius: 4px;
  background: linear-gradient(180deg, rgba(148, 163, 184, 0.8), rgba(100, 116, 139, 0.8));
}

.pipe-body {
  scrollbar-width: thin;
  scrollbar-color: rgba(148, 163, 184, 0.6) rgba(15, 23, 42, 0.24);
}

.pipe-body::-webkit-scrollbar {
  width: 8px;
}

.pipe-body::-webkit-scrollbar-thumb {
  border-radius: 4px;
  background: linear-gradient(180deg, rgba(148, 163, 184, 0.8), rgba(100, 116, 139, 0.8));
}

.subtitle-actions {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.asset-submit-btn.primary-regen {
  background: linear-gradient(135deg, #0d9488, #0f766e);
  border-color: #14b8a6;
}

.inline-audio {
  width: 100%;
  margin-top: 10px;
  border-radius: 8px;
  background: linear-gradient(135deg, #1f2b3a, #223145);
  padding: 8px;
  border: 1px solid rgba(96, 165, 250, 0.28);
}

.inline-video {
  width: 100%;
  max-height: 420px;
  margin-top: 10px;
  border-radius: 8px;
  background: #000;
  border: 1px solid rgba(167, 139, 250, 0.45);
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.24);
}

.pipeline-bubble {
  --pipe-accent: #94a3b8;
  --pipe-glow: rgba(148, 163, 184, 0.26);
  padding: 0;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: linear-gradient(165deg, #293241 0%, #202735 100%);
  max-width: 100%;
  box-shadow: 0 12px 28px rgba(7, 12, 20, 0.42);
  transition:
    transform 0.16s ease,
    box-shadow 0.16s ease,
    border-color 0.16s ease;
}

.pipeline-bubble:hover {
  transform: translateY(-1px);
  border-color: var(--pipe-accent);
  box-shadow:
    0 14px 32px rgba(7, 12, 20, 0.48),
    0 0 0 1px rgba(255, 255, 255, 0.03) inset;
}

.pipeline-bubble[data-accent='slate'] {
  --pipe-accent: #94a3b8;
  --pipe-glow: rgba(148, 163, 184, 0.24);
}
.pipeline-bubble[data-accent='violet'] {
  --pipe-accent: #a78bfa;
  --pipe-glow: rgba(167, 139, 250, 0.24);
}
.pipeline-bubble[data-accent='amber'] {
  --pipe-accent: #fbbf24;
  --pipe-glow: rgba(251, 191, 36, 0.24);
}
.pipeline-bubble[data-accent='emerald'] {
  --pipe-accent: #34d399;
  --pipe-glow: rgba(52, 211, 153, 0.24);
}
.pipeline-bubble[data-accent='sky'] {
  --pipe-accent: #38bdf8;
  --pipe-glow: rgba(56, 189, 248, 0.24);
}
.pipeline-bubble[data-accent='rose'] {
  --pipe-accent: #fb7185;
  --pipe-glow: rgba(251, 113, 133, 0.24);
}
.pipeline-bubble[data-accent='cyan'] {
  --pipe-accent: #22d3ee;
  --pipe-glow: rgba(34, 211, 238, 0.24);
}
.pipeline-bubble[data-accent='fuchsia'] {
  --pipe-accent: #e879f9;
  --pipe-glow: rgba(232, 121, 249, 0.24);
}
.pipeline-bubble[data-accent='orange'] {
  --pipe-accent: #fb923c;
  --pipe-glow: rgba(251, 146, 60, 0.24);
}

.pipe-head {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 12px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0)),
    linear-gradient(90deg, rgba(15, 23, 42, 0.62), rgba(30, 41, 59, 0.5));
  border-bottom: 1px solid rgba(148, 163, 184, 0.22);
}

.pipe-icon {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  font-size: 16px;
  line-height: 1;
  flex-shrink: 0;
  background: var(--pipe-glow);
  border: 1px solid rgba(255, 255, 255, 0.18);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.14) inset;
}

.pipe-head-text {
  min-width: 0;
}

.pipe-kicker {
  font-size: 10px;
  font-weight: 700;
  color: #c7d2e0;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.pipe-title {
  font-size: 14px;
  font-weight: 800;
  color: #eef5ff;
  text-shadow: 0 1px 0 rgba(0, 0, 0, 0.2);
}

.pipe-body {
  margin: 0;
  padding: 12px 13px 13px;
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 13px;
  line-height: 1.58;
  color: #e4ecf8;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0));
}

.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px 14px;
  border-top: 1px solid #39404a;
  background: #21262e;
}

.chip {
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  color: #d9dfe8;
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid #4a515d;
  background: #353d49;
}

.chip:hover {
  border-color: #6a7484;
  color: #f2f5f9;
  background: #394150;
}

.chip-flow {
  border-color: #6a7484;
  background: #3f2f4f;
  color: #f3e8ff;
}

.chip-flow:hover {
  border-color: #8b5cf6;
  background: #4b3560;
  color: #faf5ff;
}

.quick-sep {
  width: 1px;
  align-self: stretch;
  min-height: 28px;
  margin: 0 2px;
  background: #4b5360;
}

.chip-artifact {
  border-color: #4a515d;
  background: #37404c;
  color: #d9dfe8;
}

.chip-artifact:hover {
  border-color: #6a7484;
  background: #394150;
  color: #f2f5f9;
}

.chip-artifact-script {
  border-color: #4a515d;
  background: #37404c;
  color: #d9dfe8;
}

.chip-artifact-script:hover {
  border-color: #6a7484;
  background: #394150;
}

.chip-artifact-subtitles {
  border-color: #4a515d;
  background: #37404c;
  color: #d9dfe8;
}

.chip-artifact-subtitles:hover {
  border-color: #6a7484;
  background: #394150;
}

.chip-artifact-audios {
  border-color: #4a515d;
  background: #37404c;
  color: #d9dfe8;
}

.chip-artifact-audios:hover {
  border-color: #6a7484;
  background: #394150;
}

.chip-artifact-videos {
  border-color: #4a515d;
  background: #37404c;
  color: #d9dfe8;
}

.chip-artifact-videos:hover {
  border-color: #6a7484;
  background: #394150;
}

.artifact-bubble {
  max-width: 100%;
}

.artifact-pre {
  margin: 0;
  padding: 0;
  font-family: ui-monospace, 'Cascadia Code', 'SF Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #d7dde7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: min(420px, 55vh);
  overflow: auto;
}

.composer {
  display: block;
  padding: 14px 16px 16px;
  border-top: 1px solid #39404a;
  background: #21262e;
}

.composer-shell {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: end;
  gap: 8px;
  border: 1px solid #46505d;
  border-radius: 16px;
  background: #2f3641;
  padding: 8px;
}

.composer-actions {
  display: inline-flex;
  align-items: center;
}

.slash-panel {
  position: absolute;
  left: 48px;
  right: 44px;
  bottom: calc(100% + 8px);
  z-index: 30;
  border: 1px solid #4c5665;
  border-radius: 10px;
  background: #252c37;
  box-shadow: 0 16px 32px rgba(0, 0, 0, 0.34);
  overflow: hidden;
}

.slash-item {
  width: 100%;
  border: 0;
  background: transparent;
  color: #e5eaf2;
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
  gap: 8px;
  text-align: left;
  padding: 8px 10px;
  cursor: pointer;
}

.slash-item + .slash-item {
  border-top: 1px solid #3c4654;
}

.slash-item:hover {
  background: #313947;
}

.slash-cmd {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: #93c5fd;
}

.slash-desc {
  font-size: 12px;
  color: #cbd5e1;
}

.hidden-upload {
  display: none;
}

.icon-upload-btn {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  border: none;
  background: transparent;
  color: #aab3c0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.16s ease;
  align-self: end;
  margin-bottom: 6px;
}

.icon-upload-btn svg {
  width: 18px;
  height: 18px;
}

.icon-upload-btn:hover:not(:disabled) {
  color: #eef2f7;
  background: #3a4250;
}

.icon-upload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.composer-input :deep(.el-textarea__inner) {
  font-family: inherit;
  font-size: 14px;
  line-height: 1.55;
  border-radius: 12px;
  border: none;
  color: #e5eaf2;
  background: transparent;
  box-shadow: none;
  min-height: 74px;
  padding: 8px 0;
  text-indent: 0;
}

.composer-input :deep(.el-textarea__inner:focus) {
  border: none;
  box-shadow: none;
}

.composer-input :deep(.el-textarea__inner::placeholder) {
  color: #8f98a7;
}

.btn-green {
  --el-button-bg-color: #4d5664;
  --el-button-border-color: #4d5664;
  --el-button-text-color: #ecfdf5;
  --el-button-hover-bg-color: #3e4653;
  --el-button-hover-border-color: #3e4653;
  --el-button-active-bg-color: #353c48;
  --el-button-active-border-color: #353c48;
}

.send-icon-btn {
  width: 36px;
  height: 36px;
  border-radius: 999px;
  border: none;
  background: linear-gradient(135deg, #4d5664, #3e4653);
  color: #ecfdf5;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.32);
  align-self: end;
  margin-bottom: 6px;
}

.send-icon-btn svg {
  width: 16px;
  height: 16px;
}

.send-icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

.candidate-empty {
  border: 1px dashed #4b5360;
  border-radius: 10px;
  background: #2f3641;
  color: #99a3b1;
  font-size: 13px;
  padding: 16px;
}

@media (max-width: 1280px) {
  .shell {
    grid-template-columns: minmax(320px, 380px) minmax(0, 1fr);
  }
  .job-panel {
    display: none;
  }
}

@media (max-width: 980px) {
  .page {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }
  .wrap {
    height: auto;
  }
  .shell {
    grid-template-columns: 1fr;
  }
  .left-panel {
    border-right: none;
    border-bottom: 1px solid #434a56;
  }
  .job-panel {
    display: block;
    border-left: none;
    border-top: 1px solid #434a56;
  }
  .right-panel {
    grid-template-rows: 360px auto auto;
  }
  .asset-candidates-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .asset-candidates-list {
    grid-template-columns: 1fr;
  }
}
</style>

<style>
/* el-select 下拉 teleport 到 body，scoped 样式无法命中 */
.rerender-select-dropdown.el-popper {
  min-width: 7.5rem !important;
  border: 1px solid #4a515d !important;
  border-radius: 8px !important;
  background: #2a3039 !important;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.32) !important;
}

.rerender-voice-dropdown.el-popper {
  width: 160px !important;
  min-width: 160px !important;
  max-width: 160px !important;
}

.rerender-subtitle-dropdown.el-popper {
  width: 104px !important;
  min-width: 104px !important;
  max-width: 104px !important;
}

.rerender-select-dropdown .el-select-dropdown__item {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin: 0 !important;
  border-radius: 0 !important;
  height: 28px;
  line-height: 28px;
  padding: 0 10px;
  font-size: 12px;
  color: #d9dfe8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rerender-select-dropdown .el-select-dropdown__item.is-selected {
  color: #f2f5f9;
  font-weight: 600;
  background: #3a4250;
}

.rerender-select-dropdown .el-select-dropdown__item.is-hovering {
  background: #343b47;
}

.rerender-select-dropdown .el-select-dropdown__list {
  padding: 0 !important;
}

.rerender-select-dropdown .el-select-dropdown__wrap {
  padding: 0 !important;
}
</style>
