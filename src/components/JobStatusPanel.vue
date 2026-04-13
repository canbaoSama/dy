<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import PipelineGraph from '@/components/PipelineGraph.vue'
import { fetchJobDetail } from '@/api/factory'
import type { JobDetailResponse } from '@/types/job'

const props = defineProps<{ jobId: number | null }>()
const emit = defineEmits<{
  (e: 'status-change', payload: { jobId: number; status: string }): void
}>()

const detail = ref<JobDetailResponse | null>(null)
const loading = ref(false)
const err = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const TERMINAL = new Set(['ready_for_review', 'approved', 'failed'])

const jobStatus = computed(() => detail.value?.job.status ?? 'created')
const latestVideoUrl = computed(() => {
  if (!props.jobId) return ''
  const hasVideo = (detail.value?.videos?.length || 0) > 0
  if (!hasVideo) return ''
  return `${import.meta.env.VITE_API_BASE || '/api/v1'}/jobs/${props.jobId}/video/latest?t=${Date.now()}`
})
const latestDownloadUrl = computed(() => {
  if (!props.jobId) return ''
  const hasVideo = (detail.value?.videos?.length || 0) > 0
  if (!hasVideo) return ''
  return `${import.meta.env.VITE_API_BASE || '/api/v1'}/jobs/${props.jobId}/video/latest/download`
})

async function load() {
  if (props.jobId == null) {
    detail.value = null
    err.value = null
    return
  }
  loading.value = true
  err.value = null
  try {
    detail.value = await fetchJobDetail(props.jobId)
    emit('status-change', { jobId: props.jobId, status: detail.value.job.status })
  } catch (e) {
    detail.value = null
    err.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function clearTimer() {
  if (timer != null) {
    clearInterval(timer)
    timer = null
  }
}

function schedulePoll() {
  clearTimer()
  if (props.jobId == null) return
  timer = setInterval(() => {
    const st = detail.value?.job.status
    if (st && TERMINAL.has(st)) {
      clearTimer()
      return
    }
    void load()
  }, 2500)
}

watch(
  () => props.jobId,
  (id) => {
    clearTimer()
    if (id == null) {
      detail.value = null
      return
    }
    void load().then(() => schedulePoll())
  },
  { immediate: true },
)

onBeforeUnmount(() => clearTimer())
</script>

<template>
  <div v-if="jobId != null" class="panel">
    <div class="panel-head">
      <div class="head-left">
        <span class="pipe-label">Pipeline</span>
        <span class="job-hash">#{{ jobId }}</span>
        <span class="status-pill" :class="`pill-${jobStatus}`">{{ jobStatus }}</span>
      </div>
      <el-button size="small" type="primary" plain :loading="loading" @click="load">刷新状态</el-button>
    </div>

    <div v-if="err" class="banner banner-err" role="alert">{{ err }}</div>
    <div
      v-else-if="detail?.job.error_message && jobStatus === 'failed'"
      class="banner banner-err"
      role="alert"
    >
      {{ detail.job.error_message }}
    </div>

    <div v-if="detail" class="pipeline-wrap">
      <PipelineGraph
        :status="detail.job.status"
        :failed-stage="detail.job.failed_stage"
      />
    </div>
    <div v-else-if="!err" class="banner banner-muted">正在加载流水线…</div>

    <div v-if="detail" class="meta-grid">
      <div class="meta-card">
        <div class="meta-label">脚本版本</div>
        <div class="meta-value">{{ detail.latest_script_version ?? '—' }}</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">音频</div>
        <div class="meta-value">{{ detail.audios.length }}</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">视频</div>
        <div class="meta-value">{{ detail.videos.length }}</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">字幕表</div>
        <div class="meta-value">{{ detail.subtitle_timeline_id != null ? `#${detail.subtitle_timeline_id}` : '—' }}</div>
      </div>
    </div>
    <div v-if="latestVideoUrl" class="video-wrap">
      <div class="video-head">
        <span>最新成片预览</span>
        <a class="download-link" :href="latestDownloadUrl" download>下载视频</a>
      </div>
      <video class="video" :src="latestVideoUrl" controls preload="metadata" />
      <div class="video-link">{{ latestVideoUrl }}</div>
    </div>
  </div>
</template>

<style scoped>
.panel {
  padding: 0 0 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  background: transparent;
  min-height: 0;
}

.panel-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 16px 10px;
}

.head-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.pipe-label {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #e2e8f0;
}

.job-hash {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #93c5fd;
}

.status-pill {
  font-size: 12px;
  font-weight: 600;
  font-family: ui-monospace, 'Cascadia Code', monospace;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  background: rgba(15, 23, 42, 0.56);
  color: #e2e8f0;
}

.pill-failed {
  border-color: #ff818266;
  background: #ffebe9;
  color: #a40e26;
}

.pill-ready_for_review {
  border-color: #bf8700;
  background: #fff8c5;
  color: #7d4e00;
}

.banner {
  margin: 0 16px 10px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.5;
  border: 1px solid transparent;
}

.banner-err {
  color: #82071e;
  background: #ffebe9;
  border-color: #ff818266;
}

.banner-muted {
  color: #cbd5e1;
  background: rgba(30, 41, 59, 0.58);
  border-color: rgba(148, 163, 184, 0.35);
}

.panel :deep(.pipeline-root) {
  margin: 0;
}

.pipeline-wrap {
  margin: 0 16px;
  max-height: 220px;
  overflow-y: auto;
  overflow-x: hidden;
}

.meta-grid {
  margin: 10px 16px 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.meta-card {
  border: 1px solid rgba(148, 163, 184, 0.3);
  background: linear-gradient(165deg, rgba(15, 23, 42, 0.75), rgba(30, 41, 59, 0.62));
  border-radius: 10px;
  padding: 8px 10px;
}

.meta-label {
  font-size: 11px;
  color: #93c5fd;
  margin-bottom: 2px;
}

.meta-value {
  font-size: 15px;
  font-weight: 800;
  color: #f8fafc;
  line-height: 1.2;
}

.video-wrap {
  margin: 12px 16px 0;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(15, 23, 42, 0.55);
  border-radius: 8px;
  padding: 10px;
}

.video-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 700;
  color: #f8fafc;
  margin-bottom: 8px;
}

.download-link {
  font-size: 12px;
  color: #93c5fd;
  text-decoration: none;
}

.download-link:hover {
  text-decoration: underline;
}

.video {
  width: 100%;
  max-height: 680px;
  min-height: 420px;
  aspect-ratio: 9 / 16;
  object-fit: contain;
  background: #000;
  border-radius: 6px;
}

.video-link {
  margin-top: 6px;
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
