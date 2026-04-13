<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import JobStatusPanel from '@/components/JobStatusPanel.vue'
import { AGENT_NAME } from '@/config/app'
import { QUICK_COMMANDS } from '@/constants/commands'
import { useChatStore } from '@/store/chat'

const chatStore = useChatStore()
const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const uploadRef = ref<HTMLInputElement | null>(null)

const canSend = computed(() => input.value.trim().length > 0 && !chatStore.loading)

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

async function onSend() {
  await sendText(input.value)
}

function onClear() {
  chatStore.clear()
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
          <span v-if="chatStore.activeJobId != null" class="job-tag">任务 #{{ chatStore.activeJobId }}</span>
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
              <span class="section-sub">点击即可开始渲染</span>
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
                <div v-if="c.summary_zh" class="src">{{ c.summary_zh }}</div>
                <div class="candidate-actions">
                  <span class="src">{{ c.source }}<span v-if="c.score_10 != null"> · {{ c.score_10 }}分</span></span>
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
                  <div class="bubble" :class="item.role === 'user' ? 'is-user' : 'is-bot'">
                    <p class="text">{{ item.content }}</p>
                  </div>
                </div>
                <div v-if="item.role === 'user'" class="avatar user">我</div>
              </div>
            </template>
          </div>

          <div class="quick">
            <button v-for="q in QUICK_COMMANDS" :key="q.text" type="button" class="chip" @click="onQuickSend(q.text)">
              {{ q.label }}
            </button>
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
          <JobStatusPanel :job-id="chatStore.activeJobId" @status-change="onStatusChange" />
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
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(16, 185, 129, 0.2), transparent 55%),
    radial-gradient(1200px 700px at 100% 0%, rgba(236, 72, 153, 0.18), transparent 58%),
    linear-gradient(180deg, #0f172a 0%, #111827 55%, #0b1220 100%);
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
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0.12));
  border: 1px solid rgba(255, 255, 255, 0.26);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 18px 40px rgba(2, 6, 23, 0.35);
  backdrop-filter: blur(10px);
}

.title {
  margin: 0;
  font-size: 1.65rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #f8fafc;
  line-height: 1.25;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.job-tag {
  font-size: 13px;
  font-weight: 700;
  color: #d1fae5;
  background: rgba(16, 185, 129, 0.3);
  border: 1px solid rgba(110, 231, 183, 0.45);
  padding: 6px 12px;
  border-radius: 999px;
}

.shell {
  overflow: visible;
  display: grid;
  grid-template-columns: 460px minmax(0, 1fr) 430px;
  min-height: 0;
  gap: 12px;
}

.card {
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 14px;
  background: linear-gradient(165deg, rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.08));
  box-shadow: 0 18px 36px rgba(2, 6, 23, 0.24);
  backdrop-filter: blur(8px);
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
  grid-template-rows: minmax(0, 1fr) auto auto;
  background: rgba(30, 41, 59, 0.52);
  overflow: hidden;
}

.job-panel {
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 14px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  background: rgba(30, 41, 59, 0.56);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  box-shadow: 0 18px 36px rgba(2, 6, 23, 0.24);
}

.job-tools {
  margin: 10px;
  padding: 10px;
}

.job-tools-title {
  font-size: 12px;
  font-weight: 700;
  color: #e2e8f0;
  margin-bottom: 8px;
}

.job-select {
  width: 100%;
}

.job-select :deep(.el-input__wrapper) {
  background: rgba(15, 23, 42, 0.62);
  border: 1px solid rgba(148, 163, 184, 0.45);
  box-shadow: none;
}

.job-select :deep(.el-input__inner) {
  color: #f8fafc;
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
  color: #f8fafc;
}

.section-sub {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.86);
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
  border: 1px solid rgba(255, 255, 255, 0.22);
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.82), rgba(17, 24, 39, 0.76));
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
  background: #0969da;
  border-radius: 6px;
}

.candidate-title {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.45;
  color: #f8fafc;
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.candidate-title:hover {
  color: #bfdbfe;
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
  color: #c4b5fd;
  text-decoration: none;
}

.source-link:hover {
  text-decoration: underline;
}

.start-btn {
  border: 1px solid #34d399;
  color: #ecfdf5;
  background: linear-gradient(135deg, #10b981, #059669);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 11px;
  line-height: 1.35;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 6px 14px rgba(16, 185, 129, 0.35);
  transition: all 0.18s ease;
}

.start-btn:hover {
  background: linear-gradient(135deg, #34d399, #10b981);
  transform: translateY(-1px);
}

.src {
  color: #93c5fd;
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
  color: #1a7f37;
  background: #dafbe1;
}

.tier-mid {
  color: #7d4e00;
  background: #fff8c5;
}

.tier-bad {
  color: #a40e26;
  background: #ffebe9;
}

.chat {
  min-height: 0;
  overflow-y: auto;
  padding: 16px 16px 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.34), rgba(30, 41, 59, 0.56));
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
  border: 1px dashed rgba(148, 163, 184, 0.48);
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.42);
}

.empty-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: #f8fafc;
}

.empty-desc {
  margin: 0;
  font-size: 14px;
  color: rgba(203, 213, 225, 0.86);
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
  border: 1px solid #d0d7de;
}

.avatar.bot {
  color: #0550ae;
  background: #ddf4ff;
}

.avatar.user {
  color: #24292f;
  background: #ffffff;
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
  color: rgba(148, 163, 184, 0.92);
  margin-bottom: 4px;
}

.bubble {
  padding: 12px 14px;
  border-radius: 12px;
  line-height: 1.55;
  font-size: 14px;
}

.bubble.is-user {
  color: #eef2ff;
  background: linear-gradient(135deg, #4338ca, #7c3aed);
  border: 1px solid rgba(196, 181, 253, 0.6);
  border-bottom-right-radius: 4px;
}

.bubble.is-bot {
  color: #e2e8f0;
  background: rgba(30, 41, 59, 0.62);
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-bottom-left-radius: 4px;
}

.text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(15, 23, 42, 0.36);
}

.chip {
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  color: #dbeafe;
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid rgba(167, 139, 250, 0.4);
  background: rgba(76, 29, 149, 0.28);
}

.chip:hover {
  border-color: rgba(216, 180, 254, 0.85);
  color: #f8fafc;
  background: rgba(91, 33, 182, 0.48);
}

.composer {
  display: block;
  padding: 14px 16px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(15, 23, 42, 0.36);
}

.composer-shell {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: end;
  gap: 8px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 16px;
  background: rgba(15, 23, 42, 0.78);
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
  color: #cbd5e1;
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
  color: #f8fafc;
  background: rgba(51, 65, 85, 0.65);
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
  color: #f8fafc;
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
  color: rgba(148, 163, 184, 0.9);
}

.btn-green {
  --el-button-bg-color: #10b981;
  --el-button-border-color: #10b981;
  --el-button-text-color: #ecfdf5;
  --el-button-hover-bg-color: #059669;
  --el-button-hover-border-color: #059669;
  --el-button-active-bg-color: #047857;
  --el-button-active-border-color: #047857;
}

.send-icon-btn {
  width: 36px;
  height: 36px;
  border-radius: 999px;
  border: none;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #ecfdf5;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(16, 185, 129, 0.32);
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
  border: 1px dashed rgba(148, 163, 184, 0.48);
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.52);
  color: rgba(203, 213, 225, 0.88);
  font-size: 13px;
  padding: 16px;
}

@media (max-width: 1280px) {
  .shell {
    grid-template-columns: 360px minmax(0, 1fr);
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
    border-bottom: 1px solid #d8dee4;
  }
  .job-panel {
    display: block;
    border-left: none;
    border-top: 1px solid #d8dee4;
  }
  .right-panel {
    grid-template-rows: 360px auto auto;
  }
}
</style>
