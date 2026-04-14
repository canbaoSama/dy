export type ChatRole = 'user' | 'assistant'

export type ChatBubbleKind = 'plain' | 'pipeline' | 'code'

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  createdAt: string
  bubbleKind?: ChatBubbleKind
  /** 流水线卡片：图标、色系、阶段 key */
  pipeline?: {
    jobId: number
    stageKey: string
    title: string
    icon: string
    accent: string
  }
}
