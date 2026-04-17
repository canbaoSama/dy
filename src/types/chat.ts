export type ChatRole = 'user' | 'assistant'

export type ChatBubbleKind = 'plain' | 'pipeline' | 'code'

export interface ChatUploadPreview {
  name: string
  /** 本地上传后的缩略预览（blob:），清空会话时会 revoke */
  objectUrl: string
  media: 'image' | 'video'
}

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  createdAt: string
  bubbleKind?: ChatBubbleKind
  /** 与「已上传…」文案配套：在对话内展示刚上传的缩略图/视频 */
  uploadPreviews?: ChatUploadPreview[]
  /** 流水线卡片：图标、色系、阶段 key */
  pipeline?: {
    jobId: number
    stageKey: string
    title: string
    icon: string
    accent: string
  }
}
