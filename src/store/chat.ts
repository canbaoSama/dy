import { defineStore } from 'pinia'
import { v4 as uuidv4 } from 'uuid'
import dayjs from 'dayjs'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import {
  type CandidateLite,
  createJobFromCandidate,
  postCommand,
  translateCandidatesToZh,
  triggerIngest,
  triggerRerender,
  uploadJobAssets,
  type CommandResponse,
} from '@/api/factory'
import type { ChatMessage } from '@/types/chat'

interface ChatState {
  messages: ChatMessage[]
  loading: boolean
  activeJobId: number | null
  jobHistory: number[]
  jobStatusMap: Record<number, string>
  ingestLoading: boolean
  translatingCandidates: boolean
  lastCandidates: NonNullable<CommandResponse['candidates']> | null
  /** 递增后让 JobStatusPanel 在「已结束」状态下仍短暂轮询，捕捉重跑/多步命令触发的状态变化 */
  pipelineResumeNonce: number
}

function splitAssistantReply(reply: string): string[] {
  const raw = String(reply || '').trim()
  if (!raw) return []
  if (!raw.includes('步骤 2/2：素材候选如下')) return [raw]
  const lines = raw.split('\n')
  const step1 = lines.find((x) => x.startsWith('步骤 1/2：正文抽取完成'))
  const idx = lines.findIndex((x) => x.startsWith('步骤 2/2：素材候选如下'))
  if (!step1 || idx < 0) return [raw]
  const material = lines.slice(idx).join('\n').trim()
  return [step1, material].filter(Boolean)
}

function formatChatError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const status = e.response?.status
    if (status === 502 || status === 503) {
      return '无法连接后端（网关 502/503）。开发模式下请先在 backend 目录执行 bash run.sh，确保服务监听 http://127.0.0.1:8000，再刷新本页。'
    }
    if (!e.response && (e.code === 'ERR_NETWORK' || e.message === 'Network Error')) {
      return '网络请求失败：请确认后端已启动，且浏览器能访问与 Vite 代理一致的后端地址。'
    }
    const d = e.response?.data
    if (d && typeof d === 'object' && 'detail' in d) {
      return String((d as { detail: unknown }).detail)
    }
    return e.message || `请求失败（${status ?? '网络'}）`
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
    translatingCandidates: false,
    lastCandidates: null,
    pipelineResumeNonce: 0,
  }),

  actions: {
    async runAutoSteps(jobId: number, steps: string[]) {
      for (const step of steps) {
        try {
          const res = await postCommand(step, jobId)
          if (res.active_job_id != null) this.activeJobId = res.active_job_id
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: res.reply,
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
          })
          this.bumpPipelineResume()
        } catch (e) {
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: `自动步骤「${step}」执行失败：${formatChatError(e)}`,
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
          })
          break
        }
      }
    },

    clear() {
      for (const m of this.messages) {
        for (const u of m.uploadPreviews || []) {
          try {
            URL.revokeObjectURL(u.objectUrl)
          } catch {
            /* ignore */
          }
        }
      }
      this.messages = []
      this.lastCandidates = null
      this.activeJobId = null
      this.jobHistory = []
      this.jobStatusMap = {}
      this.pipelineResumeNonce = 0
    },

    bumpPipelineResume() {
      this.pipelineResumeNonce += 1
    },

    setActiveJobId(id: number | null) {
      this.activeJobId = id
    },

    setJobStatus(jobId: number, status: string) {
      this.jobStatusMap[jobId] = status
    },

    appendAssistantMessage(msg: ChatMessage) {
      this.messages.push(msg)
    },

    appendAssistantReply(reply: string) {
      for (const block of splitAssistantReply(reply)) {
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: block,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
      }
    },

    /** 进入页面时拉取今日候选：只追加助手消息，不插入用户气泡 */
    async bootstrapTodayCandidates() {
      if (this.loading) return
      this.loading = true
      try {
        const res = await postCommand('今日候选', this.activeJobId)
        if (res.active_job_id != null) {
          this.activeJobId = res.active_job_id
        }
        if (Array.isArray(res.candidates)) {
          this.lastCandidates = res.candidates.length > 0 ? res.candidates : null
        }
          this.appendAssistantReply(res.reply)
      } catch (e) {
        ElMessage.warning(formatChatError(e))
      } finally {
        this.loading = false
      }
    },

    async runIngest() {
      this.ingestLoading = true
      try {
        const r = await triggerIngest()
        const failedCount = r.failed_count ?? 0
        const refreshed = r.refreshed ?? 0
        const successCount = r.added + refreshed
        ElMessage.success(
          successCount > 0
            ? `抓取完成：新增 ${r.added} 条${refreshed ? `，热点刷新 ${refreshed} 条` : ''}${failedCount ? `，部分源失败 ${failedCount} 条` : ''}`
            : `抓取完成：暂无新增${failedCount ? `，失败 ${failedCount} 条` : ''}`,
        )
        const failHint =
          failedCount && r.failed?.length
            ? `\n失败源：${r.failed.slice(0, 3).map((x) => x.source).join(' / ')}${
                r.failed[0]?.error ? `\n原因示例：${String(r.failed[0].error).slice(0, 220)}` : ''
              }`
            : ''
        const hotHint =
          r.hot_top?.length
            ? `\n热度TOP：\n${r.hot_top
                .slice(0, 5)
                .map((x) => `${x.index}. 【${x.source}】${x.title}（🔥${x.heat_index ?? '-'}）`)
                .join('\n')}`
            : ''
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: `抓取完成：新增 ${r.added} 条${refreshed ? `，热点刷新 ${refreshed} 条` : ''}（信源 ${r.sources} 个）${
            failedCount ? `，部分源失败 ${failedCount} 条` : ''
          }。正在同步今日候选…${hotHint}${failHint}`,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
        if (Array.isArray(r.candidates)) {
          this.lastCandidates = r.candidates.length > 0 ? r.candidates : null
          const lines = r.candidates
            .slice(0, 15)
            .map((x, i) => `${i + 1}. 【${x.source}】${x.title_zh || x.title}（🔥${x.heat_index ?? '-'}）`)
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: lines.length
              ? `今日全球热点候选（抓取后自动更新）：\n${lines.join('\n')}`
              : '今日候选为空，请稍后重试抓取。',
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
          })
        } else {
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: '抓取已完成，但候选列表返回为空，请稍后重试。',
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
          })
        }
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.ingestLoading = false
      }
    },

    async translateCurrentCandidatesToZh() {
      if (this.translatingCandidates) return
      if (!this.lastCandidates?.length) {
        ElMessage.warning('当前没有可翻译的候选')
        return
      }
      this.translatingCandidates = true
      try {
        const translated = await translateCandidatesToZh(this.lastCandidates as CandidateLite[])
        const patchMap = new Map(translated.map((x) => [`${x.index}|||${x.url}`, x] as const))
        this.lastCandidates = this.lastCandidates.map((x) => {
          const p = patchMap.get(`${x.index}|||${x.url}`)
          if (!p) return x
          return {
            ...x,
            title_zh: p.title_zh || x.title_zh || x.title,
            summary_zh: p.summary_zh || x.summary_zh || x.summary || '',
          }
        })
        ElMessage.success('已将当前候选转为中文')
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.translatingCandidates = false
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
        const editMatch = text.match(/^(改标题|改简介|改字幕|改素材)[:：\s]+(.+)$/)
        if (this.activeJobId != null && editMatch) {
          const map: Record<string, string> = {
            改标题: '标题',
            改简介: '简介',
            改字幕: '字幕',
            改素材: '素材',
          }
          const field = map[editMatch[1]] || editMatch[1]
          const value = editMatch[2].trim()
          await triggerRerender(this.activeJobId, {
            instruction: `请仅调整${field}，要求：${value}`,
          })
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: `已记录${field}修改并触发重生成任务 #${this.activeJobId}；右侧 Pipeline 将刷新，完成后对话内会有新视频消息。`,
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
          })
          this.bumpPipelineResume()
          return
        }
        if (this.activeJobId != null && /^重新生成|^重生成/.test(text)) {
          const instruction = text.replace(/^重新生成|^重生成/, '').trim()
          await triggerRerender(this.activeJobId, { instruction })
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: `已按新需求重生成任务 #${this.activeJobId}；右侧 Pipeline 将刷新，完成后对话内会有新视频消息。`,
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
          })
          this.bumpPipelineResume()
          return
        }
        const res = await postCommand(text, this.activeJobId)
        if (res.active_job_id != null) {
          this.activeJobId = res.active_job_id
        }
        // 必须用 Array.isArray：否则 candidates: [] 时?.length 为假，会错误保留旧的英文列表
        if (Array.isArray(res.candidates)) {
          this.lastCandidates = res.candidates.length > 0 ? res.candidates : null
        }
        this.appendAssistantReply(res.reply)
        if (/^选素材\s*/.test(text) && this.activeJobId != null) {
          await this.runAutoSteps(this.activeJobId, ['生成字幕'])
        }
        if (/^确认字幕\s*/.test(text) && this.activeJobId != null) {
          await this.runAutoSteps(this.activeJobId, ['生成脚本', '字幕时间轴', '生成音频'])
        }
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
        this.activeJobId = job.id
        if (!this.jobHistory.includes(job.id)) this.jobHistory.unshift(job.id)
        this.jobStatusMap[job.id] = 'created'
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content:
            `已创建任务 #${job.id}（候选第 ${index} 条）。\n` +
            `已自动执行：创建任务 -> 正文抽取 -> 素材收集（候选展示）。\n` +
            `请你先判断素材并点击“选这个”（或发送「选素材 1,3」）。\n` +
            `选完素材后将自动继续：字幕生成（可编辑）。\n` +
            `然后由你确认：确认字幕 -> 脚本生成 -> 字幕时间轴 -> 生成音频（可试听） -> 确认音频 -> 合成视频（对话框内预览+下载）。\n` +
            `每一步结果都会在对话框内回显。`,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
        // 一键渲染后立即进入流水线（素材收集阶段），先展示素材候选待确认
        const candidateReply = await postCommand('素材候选', job.id)
        this.appendAssistantReply(candidateReply.reply)
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.loading = false
      }
    },

    async uploadAssets(
      files: File[],
      opts?: {
        previews?: Array<{ name: string; objectUrl: string; media: 'image' | 'video' }>
        silentMessage?: boolean
        replaceExisting?: boolean
      },
    ) {
      if (!files.length) return
      if (this.activeJobId == null) {
        ElMessage.warning('请先选择或创建任务，再上传素材')
        return
      }
      this.loading = true
      try {
        const r = await uploadJobAssets(this.activeJobId, files, { replaceExisting: opts?.replaceExisting })
        if (!opts?.silentMessage) {
          this.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: `已上传 ${r.added} 个素材到任务 #${this.activeJobId}。可在下方预览；点素材卡片「确认所选素材并继续」或右侧「应用并重跑」重新出片。`,
            createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
            uploadPreviews: opts?.previews?.length ? opts.previews : undefined,
          })
        }
        this.bumpPipelineResume()
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.loading = false
      }
    },

    async rerenderActiveJob(payload: {
      instruction?: string
      duration_sec?: number
      style_notes?: string
      must_use_uploaded_assets?: boolean
      prefer_video_assets?: boolean
      subtitle_tone?: string
      tts_voice?: string
      aspect_ratio?: '9:16' | '16:9'
    }) {
      if (this.activeJobId == null) {
        ElMessage.warning('请先选择任务')
        return
      }
      this.loading = true
      try {
        await triggerRerender(this.activeJobId, payload)
        this.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: `已按配置重生成任务 #${this.activeJobId}，流水线已在后台启动；右侧 Pipeline 会刷新状态，完成后对话里会出现新的「视频合成完成」。`,
          createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
        })
        this.bumpPipelineResume()
      } catch (e) {
        ElMessage.error(formatChatError(e))
      } finally {
        this.loading = false
      }
    },

    /** 连续执行若干聊天命令（不插入用户气泡），用于对话框内「确定并成片」等一键流程 */
    async runAssistantCommands(jobId: number, steps: string[]) {
      if (!steps.length) return
      this.loading = true
      this.bumpPipelineResume()
      try {
        for (let i = 0; i < steps.length; i++) {
          const step = steps[i]
          const res = await postCommand(step, jobId)
          if (res.active_job_id != null) this.activeJobId = res.active_job_id
          if (Array.isArray(res.candidates)) {
            this.lastCandidates = res.candidates.length > 0 ? res.candidates : null
          }
          this.appendAssistantReply(res.reply)
          this.bumpPipelineResume()
        }
      } catch (e) {
        ElMessage.error(formatChatError(e))
        throw e
      } finally {
        this.loading = false
      }
    },
  },
})
