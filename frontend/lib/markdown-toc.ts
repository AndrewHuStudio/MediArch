export type TocItem = {
  id: string
  level: number
  text: string
}

type Segment = { type: "text" | "code"; value: string }

// 识别标题行里的引用块（如 `[1][2][3]` 或 `[1-3]`），用于把“标题 + 正文”被写在同一行的情况拆分开
const headingCitationRegex = /\[\d+(?:\s*-\s*\d+)?(?:[\/,，、]\s*\d+)*\]/g

const splitHeadingByCitations = (content: string): { heading: string; body: string } | null => {
  const matches = Array.from(content.matchAll(headingCitationRegex))
  if (matches.length === 0) return null

  const first = matches[0]
  const last = matches[matches.length - 1]
  const textBefore = content.slice(0, first.index).trim()
  const textAfter = content.slice(last.index + last[0].length).trim()

  // 需要有标题文本以及引用块；正文可以为空（至少让引用从标题里拿出去）
  if (!textBefore) return null

  const citationBlock = content.slice(first.index, last.index + last[0].length).trim()
  const body = [citationBlock, textAfter].filter(Boolean).join(" ").trim()

  if (!body) return null

  return {
    heading: textBefore,
    body,
  }
}

const splitHeadingLine = (line: string) => {
  const match = /^(#{1,6})\s+(.+)$/.exec(line)
  if (!match) return { heading: line, body: "" }

  const marker = match[1]
  const content = match[2].trim()

  // 优先处理“标题 + 引用 + 正文”被放在同一行的情况（会导致 PDF 徽标都挤在顶部）
  const citationSplit = splitHeadingByCitations(content)
  if (citationSplit) {
    return {
      heading: `${marker} ${citationSplit.heading}`,
      body: citationSplit.body,
    }
  }
  if (content.length <= 32) return { heading: `${marker} ${content}`, body: "" }

  const sentenceMatch = /[。！？；:：]/.exec(content)
  if (sentenceMatch) {
    const idx = sentenceMatch.index
    if (idx >= 6 && idx < content.length - 1) {
      const heading = content.slice(0, idx + 1).trim()
      const body = content.slice(idx + 1).trim()
      if (heading && body) return { heading: `${marker} ${heading}`, body }
    }
  }

  const starters = [
    "根据",
    "依据",
    "按照",
    "本节",
    "本条",
    "本文",
    "本规范",
    "本标准",
    "该规范",
    "该标准",
    "该条",
    "该章",
    "说明",
    "强调",
    "提出",
    "包括",
    "包含",
    "涉及",
    "涵盖",
    "主要",
    "其中",
    "此外",
    "同时",
    "因此",
    "所以",
    "为此",
    "由于",
    "对于",
  ]

  if (content.length > 40) {
    const startSearch = content.slice(6)
    const starterMatch = new RegExp(starters.join("|")).exec(startSearch)
    if (starterMatch) {
      const idx = starterMatch.index + 6
      const heading = content.slice(0, idx).trim()
      const body = content.slice(idx).trim()
      if (heading.length >= 4 && body.length >= 8) {
        return { heading: `${marker} ${heading}`, body }
      }
    }
  }

  if (content.length > 60) {
    const idx = 32
    const heading = content.slice(0, idx).trim()
    const body = content.slice(idx).trim()
    if (heading && body) return { heading: `${marker} ${heading}`, body }
  }

  return { heading: `${marker} ${content}`, body: "" }
}

const splitByCodeFence = (text: string): Segment[] => {
  const segments: Segment[] = []
  const codeFenceRegex = /```[\s\S]*?```/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = codeFenceRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: "text", value: text.slice(lastIndex, match.index) })
    }
    segments.push({ type: "code", value: match[0] })
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < text.length) {
    segments.push({ type: "text", value: text.slice(lastIndex) })
  }

  return segments
}

const normalizeHeadingSpacing = (text: string) => {
  const normalized = text.replace(/\r\n/g, "\n")
  const segments = splitByCodeFence(normalized)

  return segments
    .map((segment) => {
      if (segment.type === "code") return segment.value

      const lines = segment.value.split("\n")
      const out: string[] = []

      for (const line of lines) {
        if (!line.trim()) {
          out.push(line)
          continue
        }

        const trimmed = line.trim()
        const headingMatch = /^(#{1,6})\s+/.exec(trimmed)

        if (headingMatch) {
          if (out.length > 0 && out[out.length - 1].trim() !== "") {
            out.push("")
          }
          const { heading, body } = splitHeadingLine(trimmed)
          out.push(heading)
          if (body) {
            out.push("")
            out.push(body)
          }
          continue
        }

        const inlineHeadingMatch = line.match(/^(.*?)(#{1,6}\s+.+)$/)
        if (inlineHeadingMatch && inlineHeadingMatch[1].trim() !== "") {
          const before = inlineHeadingMatch[1].trimEnd()
          const heading = inlineHeadingMatch[2].trimStart()
          out.push(before)
          if (out.length > 0 && out[out.length - 1].trim() !== "") {
            out.push("")
          }
          out.push(heading)
          continue
        }

        out.push(line)
      }

      return out.join("\n")
    })
    .join("")
}

const stripLeadingToc = (text: string) => {
  const lines = text.split("\n")
  let index = 0

  while (index < lines.length && lines[index].trim() === "") index++
  if (index >= lines.length) return text

  const firstLine = lines[index].trim()
  const isTocHeader = /^目录[:：]?$/.test(firstLine) || /^#{1,6}\s*目录[:：]?$/.test(firstLine)

  if (!isTocHeader) return text

  const start = index
  index += 1

  while (index < lines.length) {
    const line = lines[index].trim()
    if (!line) {
      index += 1
      continue
    }

    const isHeading = /^#{1,6}\s+/.test(line)
    if (isHeading) {
      break
    }

    const isListItem = /^[-*+]\s+/.test(line) || /^[•·]\s+/.test(line) || /^\d+[.\u3001\uFF0E]\s+/.test(line)
    const looksLikeTocLine = line.length <= 80 && !/[。！？.!?]/.test(line)
    if (isListItem || looksLikeTocLine) {
      index += 1
      continue
    }

    break
  }

  return [...lines.slice(0, start), ...lines.slice(index)].join("\n")
}

const cleanHeadingText = (raw: string) => {
  let text = raw
  text = text.replace(/\s*#+\s*$/, "")
  text = text.replace(/\s*\[(\d+)(?:\s*-\s*\d+)?\]\s*/g, "")
  text = text.replace(/\[(.+?)\]\(.+?\)/g, "$1")
  text = text.replace(/[`*_~]/g, "")
  text = text.replace(/<[^>]+>/g, "")
  text = text.replace(/\s+/g, " ").trim()
  return text
}

const injectHeadingAnchors = (text: string, idPrefix: string) => {
  const segments = splitByCodeFence(text)
  const tocItems: TocItem[] = []
  let headingIndex = 0

  const content = segments
    .map((segment) => {
      if (segment.type === "code") return segment.value

      const lines = segment.value.split("\n")
      const out: string[] = []

      for (const line of lines) {
        const trimmed = line.trim()
        const match = /^(#{1,4})\s+(.+)$/.exec(trimmed)

        if (match) {
          const level = match[1].length
          const textValue = cleanHeadingText(match[2])
          headingIndex += 1
          const id = `${idPrefix}-h${headingIndex}`

          if (textValue) {
            tocItems.push({ id, level, text: textValue })
          }

          out.push(`<a id="${id}"></a>`)
          out.push(line)
          continue
        }

        out.push(line)
      }

      return out.join("\n")
    })
    .join("")

  return { content, tocItems }
}

export const buildHeadingIdPrefix = (rawId: string, prefix = "toc") => {
  const safe = rawId.replace(/[^a-zA-Z0-9_-]/g, "")
  return safe ? `${prefix}-${safe}` : prefix
}

export const prepareMarkdownWithToc = (raw: string, idPrefix: string) => {
  const normalized = normalizeHeadingSpacing(raw)
  const stripped = stripLeadingToc(normalized)
  return injectHeadingAnchors(stripped, idPrefix)
}
