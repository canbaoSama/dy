<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import PipelineGraph from '@/components/PipelineGraph.vue'
import { fetchJobDetail } from '@/api/factory'
import type { JobDetailResponse } from '@/types/job'

const props = defineProps<{ jobId: number | null; pollKick?: number }>()
const emit = defineEmits<{
  (e: 'status-change', payload: { jobId: number; status: string }): void
  (e: 'job-detail', payload: JobDetailResponse): void
}>()

const detail = ref<JobDetailResponse | null>(null)
const loading = ref(false)
const err = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
/** 在此时间戳之前，即使任务处于「已结束」态也继续轮询，用于捕捉重跑刚启动时的状态 */
let forcedPollUntil = 0

const TERMINAL = new Set(['ready_for_review', 'approved', 'failed'])

const jobStatus = computed(() => detail.value?.job.status ?? 'created')
async function load() {
  if (props.jobId == null) {
    detail.value = null
    err.value = null
    clearTimer()
    return
  }
  loading.value = true
  err.value = null
  try {
    detail.value = await fetchJobDetail(props.jobId)
    emit('status-change', { jobId: props.jobId, status: detail.value.job.status })
    emit('job-detail', detail.value)
    const st = detail.value.job.status
    const inGrace = Date.now() < forcedPollUntil
    if (st && TERMINAL.has(st) && !inGrace) {
      clearTimer()
    } else if (timer === null) {
      schedulePoll()
    }
  } catch (e) {
    detail.value = null
    err.value = e instanceof Error ? e.message : '加载失败'
    clearTimer()
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
    const inGrace = Date.now() < forcedPollUntil
    if (st && TERMINAL.has(st) && !inGrace) {
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
    void load()
  },
  { immediate: true },
)

watch(
  () => props.pollKick ?? 0,
  (n, prev) => {
    if (props.jobId == null) return
    if (n <= 0) return
    if (prev != null && n <= prev) return
    forcedPollUntil = Date.now() + 180_000
    clearTimer()
    void load()
  },
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

  </div>
</template>

<style scoped>
.panel {
  padding: 0 0 10px;
  background: transparent;
  min-height: 0;
  height: 100%;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
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
  min-height: 0;
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
}

.pipeline-wrap::-webkit-scrollbar {
  width: 8px;
}

.pipeline-wrap::-webkit-scrollbar-thumb {
  background: #c9d1d9;
  border-radius: 99px;
}

</style>
