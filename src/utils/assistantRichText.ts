/** 将助手消息里的「【某某来源】」拆成片段，便于单独上色 */
export function splitAssistantSourceBrackets(text: string): Array<{ type: 'text' | 'source'; text: string }> {
  const re = /【[^】]*】/g
  const out: Array<{ type: 'text' | 'source'; text: string }> = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      out.push({ type: 'text', text: text.slice(last, m.index) })
    }
    out.push({ type: 'source', text: m[0] })
    last = m.index + m[0].length
  }
  if (last < text.length) {
    out.push({ type: 'text', text: text.slice(last) })
  }
  if (!out.length) {
    out.push({ type: 'text', text })
  }
  return out
}

export type AssistantRichSegment = { type: 'index' | 'source' | 'text'; text: string }

/** 候选列表等：行首「1. 」与「【来源】」单独上色，其余保持纯文本换行 */
export function splitAssistantRichSegments(text: string): AssistantRichSegment[] {
  const lines = text.split('\n')
  const out: AssistantRichSegment[] = []
  for (let i = 0; i < lines.length; i++) {
    if (i > 0) {
      out.push({ type: 'text', text: '\n' })
    }
    const line = lines[i]
    const m = line.match(/^(\d+\.\s*)(【[^】]*】)(.*)$/)
    if (m) {
      out.push({ type: 'index', text: m[1] })
      out.push({ type: 'source', text: m[2] })
      for (const b of splitAssistantSourceBrackets(m[3])) {
        out.push({ type: b.type === 'source' ? 'source' : 'text', text: b.text })
      }
    } else {
      for (const b of splitAssistantSourceBrackets(line)) {
        out.push({ type: b.type === 'source' ? 'source' : 'text', text: b.text })
      }
    }
  }
  if (!out.length) {
    out.push({ type: 'text', text })
  }
  return out
}
