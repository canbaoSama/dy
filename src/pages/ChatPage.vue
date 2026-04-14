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
import { previewTtsVoice } from '@/api/factory'
import { useChatStore } from '@/store/chat'
import type { ChatMessage } from '@/types/chat'
import type { JobDetailResponse } from '@/types/job'
import { splitAssistantRichSegments } from '@/utils/assistantRichText'
import { formatJobArtifact, type JobArtifactKind } from '@/utils/jobArtifactChat'
import { buildPipelineStageBody } from '@/utils/pipelineChat'

const chatStore = useChatStore()
/** 最近一次任务详情（与右侧 Job 面板同源），用于「脚本 / 字幕」等快捷展示 */
const lastJobDetail = ref<JobDetailResponse | null>(null)
/** 各任务上一次轮询到的状态，用于在对话里推送「阶段完成」卡片 */
const lastPollStatus = ref<Record<number, string | undefined>>({})
const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const uploadRef = ref<HTMLInputElement | null>(null)
const rerenderForm = reactive({
  duration_sec: 18,
  subtitle_tone: 'brief',
  tts_voice: 'zh-CN-XiaoxiaoNeural',
  must_use_uploaded_assets: false,
  prefer_video_assets: true,
})

const canSend = computed(() => input.value.trim().length > 0 && !chatStore.loading)
const previewingVoice = ref(false)
const previewReqSeq = ref(0)
let previewAudio: HTMLAudioElement | null = null

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
  await sendText(input.value)
}

function onClear() {
  // 保留 lastPollStatus，避免清空会话后下一轮轮询又把整条流水线重复推一遍
  chatStore.clear()
}

function assistantSegments(item: ChatMessage) {
  if (item.role !== 'assistant' || item.bubbleKind === 'pipeline') {
    return [{ type: 'text' as const, text: item.content }]
  }
  return splitAssistantRichSegments(item.content)
}

onMounted(() => {
  void chatStore.bootstrapTodayCandidates()
})

onBeforeUnmount(() => {
  if (previewAudio) {
    previewAudio.pause()
    previewAudio = null
  }
})

function pushPipelineBubble(jobId: number, stageKey: string, body: string) {
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
    const i = pipelineStageIndex(cur)
    if (i > 0) {
      for (let k = 0; k < i; k++) {
        const sk = PIPELINE_STAGES[k].key
        if (sk === 'created') continue
        pushPipelineBubble(jid, sk, buildPipelineStageBody(sk, d))
      }
    }
    if (cur === 'ready_for_review' || cur === 'approved') {
      pushPipelineBubble(jid, cur, buildPipelineStageBody(cur, d))
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
    chatStore.appendAssistantMessage({
      id: uuidv4(),
      role: 'assistant',
      content: `任务 #${jid} 已重新进入流水线，将分步推送进度。`,
      createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    })
    handleJobDetail(d)
    return
  }

  if (prev !== 'created') {
    pushPipelineBubble(jid, prev, buildPipelineStageBody(prev, d))
  }
  lastPollStatus.value[jid] = cur
  } catch (e) {
    console.error('handleJobDetail', e)
  }
}

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

function onPickAssets() {
  uploadRef.value?.click()
}

async function onAssetsChange(e: Event) {
  const el = e.target as HTMLInputElement
  const files = Array.from(el.files || [])
  if (!files.length) return
  await chatStore.uploadAssets(files)
  el.value = ''
}

function bumpRerenderDuration(delta: number) {
  rerenderForm.duration_sec = Math.min(22, Math.max(12, Math.round(Number(rerenderForm.duration_sec) + delta)))
}

async function onRerenderWithConfig() {
  await chatStore.rerenderActiveJob({
    duration_sec: rerenderForm.duration_sec,
    subtitle_tone: rerenderForm.subtitle_tone,
    tts_voice: rerenderForm.tts_voice,
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
              <span class="section-title">候选</span>
              <span class="section-sub">美东当日热点优先 · 点击一键渲染</span>
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
                    <span v-if="c.heat_index != null"> · 热度指数 {{ c.heat_index }}</span>
                    <span v-else-if="c.score_10 != null"> · 热度 {{ c.score_10 }}</span>
                    <span v-if="c.rank_score_10 != null"> · 综合 {{ c.rank_score_10 }}</span>
                    <span v-if="c.source_weight != null"> · 来源权重 {{ c.source_weight }}</span>
                    <span v-if="c.recency_score_10 != null"> · 时效 {{ c.recency_score_10 }}</span>
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
              <span class="rerender-bar-title">重生成配置</span>
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
                  <div v-else class="bubble" :class="item.role === 'user' ? 'is-user' : 'is-bot'">
                    <p v-if="item.role === 'user'" class="text">{{ item.content }}</p>
                    <p v-else class="text plain-rich">
                      <template v-for="(seg, si) in assistantSegments(item)" :key="si">
                        <span v-if="seg.type === 'index'" class="seg-index">{{ seg.text }}</span>
                        <span v-else-if="seg.type === 'source'" class="seg-source">{{ seg.text }}</span>
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
            <button v-for="q in QUICK_COMMANDS" :key="q.text" type="button" class="chip" @click="onQuickSend(q.text)">
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
                @click="onPickAssets"
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
                placeholder="给我指令，Enter 发送，Shift+Enter 换行"
                resize="none"
                @keydown.enter="onInputEnter"
              />
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
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
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
  overflow-y: auto;
  overflow-x: hidden;
  background: #262a31;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
}

.job-tools {
  margin: 10px;
  padding: 10px;
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
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
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

.pipeline-bubble {
  padding: 0;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid #434a56;
  background: #303742;
  max-width: 100%;
}

.pipeline-bubble[data-accent='slate'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='violet'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='amber'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='emerald'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='sky'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='rose'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='cyan'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='fuchsia'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}
.pipeline-bubble[data-accent='orange'] {
  border-color: #49515f;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.2);
}

.pipe-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: linear-gradient(90deg, #3a424f, #313946);
  border-bottom: 1px solid #444c59;
}

.pipe-icon {
  font-size: 22px;
  line-height: 1;
  flex-shrink: 0;
}

.pipe-head-text {
  min-width: 0;
}

.pipe-kicker {
  font-size: 11px;
  font-weight: 700;
  color: #aab3c0;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.pipe-title {
  font-size: 14px;
  font-weight: 800;
  color: #e8ecf3;
}

.pipe-body {
  margin: 0;
  padding: 12px 14px 14px;
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 13px;
  line-height: 1.55;
  color: #d7dde7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
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
