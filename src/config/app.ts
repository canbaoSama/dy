/**
 * Web 页是「自然语言运营层」的一种适配器（HTTP），与 MVP 文档中的 Telegram/飞书 Bot 同级；
 * 生产内核为 FastAPI，未来可并行接 QClaw 连接器调用同一套 /api/v1。
 */
export const APP_TITLE = '海外新闻视频工厂 · MVP'

export const AGENT_NAME = '运营助手'

export const APP_SUBTITLE =
  '先「抓取新闻」，再「今天候选」→「做第 N 条」→「开始渲染」。下方面板轮询任务状态（与完整方案任务编排层对齐）。'
