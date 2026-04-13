import { defineStore } from 'pinia'
import { v4 as uuidv4 } from 'uuid'
import dayjs from 'dayjs'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { createJobFromCandidate, postCommand, triggerIngest, triggerPipeline, uploadJobAssets, type CommandResponse } from '@/api/factory'
import type { ChatMessage } from '@/types/chat'

interface ChatState {
  messages: ChatMessage[]
  loading: boolean
  activeJobId: number | null
  jobHistory: number[]
  jobStatusMap: Record<number, string>
  ingestLoading: boolean
  lastCandidates: NonNullable<CommandResponse['candidates']> | null
}

function formatChatError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const d = e.response?.data
    if (d && typeof d === 'object' && 'detail' in d) {
      return String((d as { detail: unknown }).detail)
    }
    return e.message || `请求失败（${e.response?.status ?? '网络'}）`
  }
  if (e instanceof Error) return e.message
  return '发送失败'
}

export const useChatStore = defineStore('chat', {
  state: (): ChatState => ({
    messages: [],
    loading: false,
    activeJobId: null,
    jobHistory: [],
    jobStatusMap: {},
    ingestLoading: false,
    lastCandidates: null,
  }),

  actions: {
    clear() {
      this.messages = []
      this.lastCandidates = null
      this.activeJobId = null
      this.jobHistory = []
      this.jobStatusMap = {}
    },

    setActiveJobId(id: number | null) {
      this.activeJobId = id
    },

    setJobStatus(jobId: number, status: string) {
      this.jobStatusMap[jobId] = status
    },

    async runIngest() {
      this.ingestLoading = true
      try {
        const r = await triggerIngest()
        const failedCount = r.failed_count ?? 0
        ElMessage.success(`抓取完成：新增 ${r.added} 条${failedCount ? `，失败 ${failedCount} 条` : ''}`)
        const failHint =
          failedCount && r.failed?.length
            ? `\n失败源：${r.failed.slice(0, 3).map((x) => x.source).join(' / ')}`
            : ''
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: `抓取完成：新增 ${r.added} 条（信源 ${r.sources} 个）${
            failedCount ? `，失败 ${failedCount} 条` : ''
          }。可发送「今天候选」。${failHint}`,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.ingestLoading = false
      }
    },

    async ask(content: string) {
      const text = content.trim()
      if (!text || this.loading) return

      this.messages.push({
        id: uuidv4(),
        role: 'user',
        content: text,
        createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      })

      this.loading = true
      try {
        const res = await postCommand(text, this.activeJobId)
        if (res.active_job_id != null) {
          this.activeJobId = res.active_job_id
        }
        this.lastCandidates = res.candidates?.length ? res.candidates : this.lastCandidates
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: res.reply,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
      } catch (e) {
        ElMessage.error(formatChatError(e))
        this.messages.pop()
      } finally {
        this.loading = false
      }
    },

    async startFromCandidate(index: number) {
      if (this.loading) return
      if (this.activeJobId != null) {
        const st = this.jobStatusMap[this.activeJobId]
        const running = st && !['ready_for_review', 'approved', 'failed'].includes(st)
        if (running) {
          ElMessage.warning(`任务 #${this.activeJobId} 正在运行，请先等待完成或切换查看`)
          return
        }
      }
      this.loading = true
      try {
        const { job } = await createJobFromCandidate(index)
        await triggerPipeline(job.id)
        this.activeJobId = job.id
        if (!this.jobHistory.includes(job.id)) this.jobHistory.unshift(job.id)
        this.jobStatusMap[job.id] = 'created'
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: `已开始处理第 ${index} 条，任务 #${job.id} 已进入流水线。`,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.loading = false
      }
    },

    async uploadAssets(files: File[]) {
      if (!files.length) return
      if (this.activeJobId == null) {
        ElMessage.warning('请先选择或创建任务，再上传素材')
        return
      }
      this.loading = true
      try {
        const r = await uploadJobAssets(this.activeJobId, files)
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: `已上传 ${r.added} 个素材到任务 #${this.activeJobId}，可直接重新点“渲染”或触发流水线。`,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.loading = false
      }
    },
  },
})
