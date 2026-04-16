/** 与后端 command_parser 自然语言入口对齐的快捷填充文案 */
export const QUICK_COMMANDS = [
  { label: '今日候选', text: '今日候选', kind: 'normal' },
  { label: '素材候选', text: '素材候选', kind: 'flow' },
  { label: '生成字幕', text: '生成字幕', kind: 'flow' },
  { label: '确认字幕', text: '确认字幕', kind: 'flow' },
  { label: '确认音频', text: '确认音频', kind: 'flow' },
  { label: '给我标题', text: '给我标题', kind: 'normal' },
  { label: '给我简介', text: '给我简介', kind: 'normal' },
] as const
