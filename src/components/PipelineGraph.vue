<script setup lang="ts">
import { computed } from 'vue'
import { PIPELINE_STAGES, computeStageStates, type StageVisualState } from '@/constants/pipeline'

const props = defineProps<{
  status: string
  failedStage?: string | null
}>()

const states = computed(() => computeStageStates(props.status, props.failedStage ?? null))

function stageWrapClass(s: StageVisualState) {
  return `wrap-${s}`
}

function stAt(i: number): StageVisualState {
  return states.value[i] ?? 'pending'
}
</script>

<template>
  <div class="pipeline-root" role="list" aria-label="生产流水线">
    <div class="pipeline-list">
      <template v-for="(st, i) in PIPELINE_STAGES" :key="st.key">
        <div class="stage-row" :class="stageWrapClass(stAt(i))">
          <div class="node" :title="st.key">
            <!-- pending -->
            <svg
              v-if="stAt(i) === 'pending'"
              class="ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <circle cx="12" cy="12" r="9" />
            </svg>
            <!-- running -->
            <svg
              v-else-if="stAt(i) === 'running'"
              class="ico ico-spin"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <circle cx="12" cy="12" r="9" stroke-dasharray="44" stroke-dashoffset="12" stroke-linecap="round" />
            </svg>
            <!-- success -->
            <svg
              v-else-if="stAt(i) === 'success'"
              class="ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M8 12l2.5 2.5L16 9" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <!-- failed -->
            <svg
              v-else-if="stAt(i) === 'failed'"
              class="ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M9 9l6 6M15 9l-6 6" stroke-linecap="round" />
            </svg>
            <!-- skipped -->
            <svg v-else class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="9" stroke-dasharray="4 3" />
              <path d="M8 12h8" stroke-linecap="round" />
            </svg>
          </div>
          <div class="stage-content">
            <div class="label">{{ st.label }}</div>
            <div class="key">{{ st.key }}</div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.pipeline-root {
  width: 100%;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  background: #ffffff;
  overflow: hidden;
}

.pipeline-list {
  padding: 10px 12px;
  max-height: none;
  overflow: visible;
}

.stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 4px;
  border-bottom: 1px dashed #e5e7eb;
}

.stage-row:last-child {
  border-bottom: none;
}

.node {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid #6e7781;
  background: #ffffff;
  color: #57606a;
  box-sizing: border-box;
}

.ico {
  width: 22px;
  height: 22px;
}

.ico-spin {
  animation: spin 0.85s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.wrap-pending .node {
  border-color: #8c959f;
  color: #6e7781;
  background: #f6f8fa;
}

.wrap-running .node {
  border-color: #0969da;
  color: #0969da;
  background: #ddf4ff;
  box-shadow: 0 0 0 3px rgba(9, 105, 218, 0.15);
}

.wrap-success .node {
  border-color: #1a7f37;
  color: #1a7f37;
  background: #dafbe1;
}

.wrap-failed .node {
  border-color: #cf222e;
  color: #cf222e;
  background: #ffebe9;
}

.wrap-skipped .node {
  border-color: #afb8c1;
  color: #6e7781;
  background: #f6f8fa;
}

.label {
  font-size: 13px;
  font-weight: 700;
  line-height: 1.35;
  color: #1f2328;
}

.key {
  font-size: 11px;
  color: #6b7280;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.stage-content {
  min-width: 0;
}

.wrap-pending .label {
  color: #424a53;
}

.wrap-skipped .label {
  color: #6e7781;
  font-weight: 500;
}
</style>
