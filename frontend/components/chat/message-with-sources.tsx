"use client"

import { useState, useRef, useEffect, useMemo, useId } from "react"
import { motion } from "framer-motion"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { PDFCitationBadge, type PDFSource } from "./pdf-citation-badge"
import { PDFViewerModal } from "./pdf-viewer-modal"
import { ImageLightbox } from "@/components/ui/image-lightbox"
import { buildHeadingIdPrefix, prepareMarkdownWithToc } from "@/lib/markdown-toc"
import { getPdfThumbnail } from "@/lib/pdf-thumbnails"

interface MarkdownContentProps {
  content: string
  images?: string[]
  sources?: PDFSource[]
  onCitationPositions?: (positions: Array<{ number: number; top: number }>) => void
  onCitationClick?: (citationNumber: number) => void
  positionAnchorRef?: React.RefObject<HTMLElement>
}

const citationClassName =
  "inline-flex items-center align-middle text-blue-200 text-[11px] font-semibold px-1.5 py-0.5 rounded-md bg-blue-500/10 border border-blue-400/30 cursor-pointer hover:bg-blue-500/20 transition-colors mx-0.5 leading-none"

const parseCitationNumbers = (raw: string | null): number[] => {
  if (!raw) return []
  const parts = raw
    .split(/[,\s/，、]+/)
    .map((t) => t.trim())
    .filter(Boolean)

  const numbers: number[] = []

  for (const part of parts) {
    if (part.includes("-")) {
      const [startRaw, endRaw] = part.split("-", 2)
      const start = Number.parseInt(startRaw, 10)
      const end = Number.parseInt(endRaw, 10)
      if (!Number.isFinite(start)) continue
      if (!Number.isFinite(end)) {
        numbers.push(start)
        continue
      }
      const min = Math.min(start, end)
      const max = Math.max(start, end)
      for (let n = min; n <= max; n++) numbers.push(n)
      continue
    }

    const single = Number.parseInt(part, 10)
    if (Number.isFinite(single)) numbers.push(single)
  }

  return numbers
}

const normalizeCitationNumbers = (numbers: number[], maxCitation: number) => {
  const filtered = numbers.filter((num) => Number.isFinite(num) && num > 0 && num <= maxCitation)
  const ordered: number[] = []
  const seen = new Set<number>()
  filtered.forEach((num) => {
    if (!seen.has(num)) {
      seen.add(num)
      ordered.push(num)
    }
  })
  return ordered
}

const extractCitationNumbers = (raw: string) => {
  const matches = raw.matchAll(/\[(\d+(?:\s*-\s*\d+)?(?:[\/,，、]\s*\d+)*)\]/g)
  const numbers: number[] = []
  for (const match of matches) {
    numbers.push(...parseCitationNumbers(match[1]))
  }
  return numbers
}

const buildCitationTag = (number: number) =>
  `<span data-citation="${number}" class="${citationClassName}">${number}</span>`

const buildCitationTags = (numbers: number[], maxCitation: number) => {
  const normalized = normalizeCitationNumbers(numbers, maxCitation)
  if (normalized.length === 0) return ""
  return normalized.map(buildCitationTag).join("")
}

const buildCitationTokens = (numbers: number[]) => numbers.map((num) => `[${num}]`).join("")

const distributeTrailingCitations = (line: string) => {
  const trailingCluster = /((?:\s*\[(?:\d+\s*-\s*\d+|\d+)\]\s*)+)$/
  const match = trailingCluster.exec(line)
  if (!match) return line

  const cluster = match[1]
  const numbers = extractCitationNumbers(cluster)
  if (numbers.length <= 1) return line

  const body = line.slice(0, match.index).trimEnd()
  if (!body) return line

  const sentences = body.match(/[^。！？.!?；;]+[。！？.!?；;]*/g) || [body]
  if (sentences.length <= 1) {
    return `${body} ${buildCitationTokens(numbers)}`
  }

  const grouped: number[][] = Array.from({ length: sentences.length }, () => [])
  numbers.forEach((num, index) => {
    const target = Math.min(index, sentences.length - 1)
    grouped[target].push(num)
  })

  return sentences
    .map((sentence, index) => {
      const tokens = grouped[index]
      if (tokens.length === 0) return sentence
      return `${sentence.trimEnd()} ${buildCitationTokens(tokens)}`
    })
    .join("")
}

const distributeCitationClusters = (text: string) => {
  const paragraphs = text.split(/\n{2,}/)
  return paragraphs
    .map((paragraph) => {
      if (!paragraph.trim()) return paragraph

      const lines = paragraph.split("\n")
      const hasListLine = lines.some((line) => /^\s*([-*+]|(\d+)[.、])\s+/.test(line))
      if (!hasListLine) {
        const trimmed = paragraph.trimStart()
        if (trimmed.startsWith("<a id=") || /^\s*#{1,6}\s+/.test(trimmed)) {
          return paragraph
        }
        return distributeTrailingCitations(paragraph)
      }

      return lines
        .map((line) => {
          if (/^\s*#{1,6}\s+/.test(line) || line.startsWith("<a id=")) {
            return line
          }
          return distributeTrailingCitations(line)
        })
        .join("\n")
    })
    .join("\n\n")
}

const replaceCitations = (input: string, maxCitation: number) => {
  if (maxCitation <= 0) return input

  return input
    .replace(/(?:\[(?:\d+\s*-\s*\d+|\d+)\]\s*){2,}/g, (raw) => buildCitationTags(extractCitationNumbers(raw), maxCitation))
    .replace(/\[(\d+(?:\s*-\s*\d+)?(?:[\/,，、]\s*\d+)*)\]/g, (raw, content) => {
      const numbers = parseCitationNumbers(content)
      const rendered = buildCitationTags(numbers, maxCitation)
      return rendered || raw
    })
}

const applyCitationMarkup = (text: string, maxCitation: number) => {
  if (maxCitation <= 0) return text

  const codeFenceRegex = /```[\s\S]*?```/g
  let lastIndex = 0
  let match
  let result = ""

  while ((match = codeFenceRegex.exec(text)) !== null) {
    const segment = distributeCitationClusters(text.slice(lastIndex, match.index))
    result += replaceCitations(segment, maxCitation)
    result += match[0]
    lastIndex = match.index + match[0].length
  }

  result += replaceCitations(distributeCitationClusters(text.slice(lastIndex)), maxCitation)
  return result
}

const markdownComponents: Components = {
  h1: ({ node: _node, ...props }) => (
    <h1
      className="text-3xl md:text-4xl font-bold mt-7 mb-4 tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-blue-400 via-indigo-400 to-purple-400"
      {...props}
    />
  ),
  h2: ({ node: _node, ...props }) => (
    <h2 className="text-2xl font-semibold mt-6 mb-3 text-cyan-200 border-b border-white/10 pb-1 tracking-tight" {...props} />
  ),
  h3: ({ node: _node, ...props }) => (
    <h3 className="text-lg font-semibold mt-5 mb-2 text-sky-200/90 tracking-tight" {...props} />
  ),
  h4: ({ node: _node, ...props }) => (
    <h4 className="text-sm font-semibold mt-3 mb-1.5 text-blue-200 uppercase tracking-wide" {...props} />
  ),
  p: ({ node: _node, ...props }) => <p className="my-3 whitespace-pre-wrap leading-relaxed text-[15px] text-gray-100" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="list-disc ml-5 my-3 space-y-1.5 text-gray-100 leading-relaxed" {...props} />,
  ol: ({ node: _node, ...props }) => <ol className="list-decimal ml-5 my-3 space-y-1.5 text-gray-100 leading-relaxed" {...props} />,
  li: ({ node: _node, ...props }) => <li className="my-1 leading-relaxed" {...props} />,
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="border-l-2 border-blue-500/50 pl-3 my-3 text-gray-300 italic bg-white/5 rounded-lg py-2" {...props} />
  ),
  hr: ({ node: _node, ...props }) => <hr className="border-white/10 my-6" {...props} />,
  a: ({ node: _node, ...props }) => <a className="text-blue-400 hover:text-blue-300 underline" {...props} />,
  pre: ({ node: _node, ...props }) => <>{props.children}</>,
  strong: ({ node: _node, ...props }) => (
    <strong className="text-white font-semibold bg-white/5 rounded-sm px-0.5 tracking-tight" {...props} />
  ),
  em: ({ node: _node, ...props }) => <em className="text-gray-200" {...props} />,
  code: ({ node: _node, className, children, ...props }) => {
    const raw = String(children)
    const match = /language-(\w+)/.exec(className || "")
    const isBlock = Boolean(match) || raw.includes("\n")

    if (!isBlock) {
      return (
        <code className="bg-gray-700/50 px-1.5 py-0.5 rounded text-sm font-mono text-blue-300" {...props}>
          {children}
        </code>
      )
    }

    const lang = match?.[1] || "text"
    const codeText = raw.replace(/\n$/, "")

    return (
      <div className="my-3 rounded-lg overflow-hidden bg-gray-900 border border-gray-700">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-800/50 border-b border-gray-700">
          <span className="text-xs text-gray-400 font-mono">{lang}</span>
        </div>
        <pre className="p-4 overflow-x-auto">
          <code className="text-sm font-mono text-gray-100">{codeText}</code>
        </pre>
      </div>
    )
  },
  table: ({ node: _node, className, children, ...props }) => (
    <div className="my-4 overflow-x-auto">
      <table
        className={[
          "min-w-full border-collapse border border-white/20 rounded-lg overflow-hidden",
          className,
        ]
          .filter(Boolean)
          .join(" ")}
        {...props}
      >
        {children}
      </table>
    </div>
  ),
  thead: ({ node: _node, ...props }) => <thead className="bg-white/10" {...props} />,
  th: ({ node: _node, ...props }) => (
    <th className="border border-white/20 px-4 py-2 text-left text-sm font-semibold text-white" {...props} />
  ),
  td: ({ node: _node, ...props }) => (
    <td className="border border-white/20 px-4 py-2 text-sm text-gray-300" {...props} />
  ),
}

export function MarkdownContent({ content, images, sources, onCitationPositions, onCitationClick, positionAnchorRef }: MarkdownContentProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!sources || sources.length === 0 || !onCitationPositions) return

    const updatePositions = () => {
      const positions: Array<{ number: number; top: number }> = []

      const container = contentRef.current
      if (!container) return

      const containerRect = container.getBoundingClientRect()
      const anchorRect = positionAnchorRef?.current?.getBoundingClientRect() || containerRect
      const positionsByNumber = new Map<number, number>()

      // [FIX 2026-01-14] 通过 DOM 查询获取 citation 的首次出现位置（避免 citations 被拆成独立 React 节点导致换行/错位）
      // [FIX 2026-01-18] 优化位置计算，让卡片随段落自然出现
      const citationElements = Array.from(container.querySelectorAll<HTMLElement>("[data-citation]"))
      citationElements.forEach((el) => {
        const raw = el.getAttribute("data-citation")
        const numbers = parseCitationNumbers(raw)
        if (numbers.length === 0) return
        const rect = el.getBoundingClientRect()
        const relativeTop = rect.top - anchorRect.top
        numbers.forEach((citationNumber) => {
          if (!positionsByNumber.has(citationNumber)) {
            positionsByNumber.set(citationNumber, relativeTop)
          }
        })
      })

      for (let citationNumber = 1; citationNumber <= sources.length; citationNumber++) {
        const top = positionsByNumber.get(citationNumber)
        if (typeof top === "number") {
          positions.push({ number: citationNumber, top })
        }
      }

      // [修复 2026-01-18] 不强制调整位置，保持引用的原始位置
      // 只在引用位置非常接近时（小于20px）才微调，避免完全重叠
      positions.sort((a, b) => a.top - b.top)

      const minSpacing = 28
      for (let i = 1; i < positions.length; i++) {
        const prev = positions[i - 1]
        const curr = positions[i]
        if (curr.top - prev.top < minSpacing) {
          curr.top = prev.top + minSpacing
        }
      }

      onCitationPositions(positions)
    }

    requestAnimationFrame(updatePositions)
  }, [content, sources, onCitationPositions, positionAnchorRef])

  const maxCitation = sources?.length ?? 0

  const renderMarkdownSegment = (segment: string, key: string) => (
    <ReactMarkdown
      key={key}
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw]}
      components={markdownComponents}
    >
      {applyCitationMarkup(segment, maxCitation)}
    </ReactMarkdown>
  )

  const parseContentWithCitationsAndImages = (text: string) => {
    const parts: React.ReactNode[] = []
    // [FIX 2026-01-14] 仅拆分图片占位符；引用 `[n]` 交给 Markdown 渲染转为 <sup data-citation>
    const regex = /\[image:(\d+)\]/g
    let lastIndex = 0
    let match
    let keyIndex = 0

    while ((match = regex.exec(text)) !== null) {
      if (lastIndex < match.index) {
        const textBefore = text.slice(lastIndex, match.index)
        parts.push(renderMarkdownSegment(textBefore, `text-${keyIndex++}`))
      }

      if (match[1]) {
        const imgIdx = parseInt(match[1])
        if (images && images[imgIdx]) {
          // 尝试从 sources 中找到对应的图片来源
          const imageSource = sources?.find((s, idx) =>
            s.contentType === 'image' && images[imgIdx]?.includes(s.id || '')
          ) || sources?.find(s => s.contentType === 'image')
          const altText = imageSource
            ? `${imageSource.title} · 第 ${imageSource.pageNumber} 页${imageSource.section ? ` · ${imageSource.section}` : ""}`
            : `图 ${imgIdx + 1}: 来自数据库的相关资料`

          parts.push(
            <motion.div
              key={`image-${imgIdx}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: keyIndex * 0.05 }}
              className="my-4"
            >
              <ImageLightbox
                src={images[imgIdx] || "/placeholder.svg"}
                alt={altText}
              />
              {/* 图片来源标注 - 仅显示来源信息 */}
              {imageSource && (
                <div className="mt-2 text-[11px] text-gray-500 leading-snug pl-2 border-l-2 border-white/10 bg-white/5 rounded">
                  来源：{imageSource.title} · 第 {imageSource.pageNumber} 页
                  {imageSource.section && ` · ${imageSource.section}`}
                </div>
              )}
            </motion.div>
          )
        }
      }

      lastIndex = match.index + match[0].length
      keyIndex++
    }

    if (lastIndex < text.length) {
      const remainingText = text.slice(lastIndex)
      parts.push(renderMarkdownSegment(remainingText, "text-final"))
    }

    return parts.length > 0 ? parts : renderMarkdownSegment(text, "text-all")
  }

  return (
    <div
      ref={contentRef}
      className="prose prose-invert prose-sm max-w-none leading-relaxed tracking-wide"
      onClick={(e) => {
        const target = e.target as HTMLElement | null
        const badge = target?.closest?.('[data-citation]') as HTMLElement | null
        const raw = badge?.getAttribute?.("data-citation") ?? null
        const citationNumber = parseCitationNumbers(raw)[0]
        if (Number.isFinite(citationNumber) && citationNumber > 0) {
          onCitationClick?.(citationNumber)
        }
      }}
    >
      {parseContentWithCitationsAndImages(content)}
    </div>
  )
}

export function MessageWithSources({ content, sources, images }: MarkdownContentProps & { sources?: PDFSource[] }) {
  const [selectedPDF, setSelectedPDF] = useState<PDFSource | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [citationPositions, setCitationPositions] = useState<Array<{ number: number; top: number }>>([])
  const messageRef = useRef<HTMLDivElement>(null)
  const tocRef = useRef<HTMLDivElement>(null)
  const tocId = useId()
  const headingIdPrefix = useMemo(() => buildHeadingIdPrefix(tocId, "toc"), [tocId])
  const { content: contentWithToc, tocItems } = useMemo(
    () => prepareMarkdownWithToc(content, headingIdPrefix),
    [content, headingIdPrefix]
  )

  // [FIX 2026-01-14] 增强引用验证和数据清洗逻辑
  // 关键改进：
  // 1. 验证每个source的必需字段（title, pageNumber, snippet）
  // 2. 过滤掉无效的引用（缺少关键字段）
  // 3. 确保引用索引与实际可用的sources一一对应
  const { normalizedContent, normalizedSources } = useMemo(() => {
    if (!sources || sources.length === 0) return { normalizedContent: contentWithToc, normalizedSources: sources }

    // 验证并过滤sources
    const validSources = sources.filter((source, index) => {
      // 必需字段验证
      const hasTitle = Boolean(source.title && source.title.trim())
      const hasPageNumber = typeof source.pageNumber === 'number' && source.pageNumber > 0
      const hasSnippet = Boolean(source.snippet && source.snippet.trim())

      // 至少需要title和pageNumber
      if (!hasTitle || !hasPageNumber) {
        console.warn(
          `[Citation Warning] Source at index ${index} is missing required fields:`,
          { hasTitle, hasPageNumber, title: source.title, pageNumber: source.pageNumber }
        )
        return false
      }

      return true
    })

    // 检查是否有无效引用（索引超出 validSources 范围）
    const rx = /\[(\d+)(?:\s*-\s*(\d+))?\]/g
    const invalidCitations: number[] = []
    let match: RegExpExecArray | null

    while ((match = rx.exec(contentWithToc)) !== null) {
      const start = parseInt(match[1], 10)
      const end = match[2] ? parseInt(match[2], 10) : start
      if (!Number.isFinite(start) || start <= 0) continue
      const max = Number.isFinite(end) && end > 0 ? Math.max(start, end) : start
      for (let n = start; n <= max; n++) {
        if (n > validSources.length && !invalidCitations.includes(n)) {
          invalidCitations.push(n)
        }
      }
    }

    // 输出警告（如果有无效引用）
    if (invalidCitations.length > 0) {
      console.warn(
        `[Citation Warning] Backend returned invalid citation indices: [${invalidCitations.join(', ')}]. ` +
        `Valid sources count: ${validSources.length}. These citations will be preserved as-is.`
      )
    }

    // 如果过滤后sources数量变化，输出日志
    if (validSources.length !== sources.length) {
      console.info(
        `[Citation Info] Filtered sources: ${sources.length} → ${validSources.length} (removed ${sources.length - validSources.length} invalid sources)`
      )
    }

    return { normalizedContent: contentWithToc, normalizedSources: validSources }
  }, [contentWithToc, sources])

  const [resolvedSources, setResolvedSources] = useState<PDFSource[] | undefined>(normalizedSources)

  useEffect(() => {
    setResolvedSources(normalizedSources)
  }, [normalizedSources])

  useEffect(() => {
    let cancelled = false
    const loadThumbnails = async () => {
      if (!normalizedSources || normalizedSources.length === 0) {
        setResolvedSources(normalizedSources)
        return
      }

      const updated = await Promise.all(
        normalizedSources.map(async (source) => {
          if (source.thumbnail) return source
          if (source.imageUrl) return { ...source, thumbnail: source.imageUrl }

          const pdfUrl = source.pdfUrl
          if (!pdfUrl) return source

          const thumb = await getPdfThumbnail(pdfUrl, 1, 320)
          if (thumb) {
            return { ...source, thumbnail: thumb }
          }

          return source
        })
      )

      if (!cancelled) {
        setResolvedSources(updated)
      }
    }

    void loadThumbnails()
    return () => {
      cancelled = true
    }
  }, [normalizedSources])

  const sourcesForRender = resolvedSources ?? normalizedSources

  const handlePDFClick = (source: PDFSource) => {
    setSelectedPDF(source)
    setIsModalOpen(true)
  }

  const handleCitationClick = (citationNumber: number) => {
    if (!sourcesForRender || sourcesForRender.length === 0) return
    const source = sourcesForRender[citationNumber - 1]
    if (!source) return
    handlePDFClick(source)
  }

  const scrollToToc = () => {
    tocRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
  }

  const scrollToHeading = (id: string) => {
    const target = document.getElementById(id)
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }

  const getTocIndentClass = (level: number) => {
    if (level <= 2) return "pl-3"
    if (level === 3) return "pl-6"
    return "pl-9"
  }

  // [FIX 2026-01-14] 侧边PDF资料徽标位置兜底
  // 说明：当某些 source 没有在正文中出现 `[n]` 时，仍然在侧边栏展示（与“参考资料”一致）。
  const computedBadgePositions = useMemo(() => {
    const sourcesCount = sourcesForRender?.length || 0
    if (sourcesCount === 0) return []

    const minSpacing = 48
    const fallbackSpacing = 88

    const byNumber = new Map<number, number>()
    citationPositions.forEach((p) => {
      if (typeof p.number === "number" && typeof p.top === "number") {
        byNumber.set(p.number, p.top)
      }
    })

    const positions: Array<{ number: number; top: number }> = []

    // 如果正文没有找到任何引用位置，则均匀分布在视图中，避免全部挤在顶部
    if (byNumber.size === 0) {
      for (let n = 1; n <= sourcesCount; n++) {
        positions.push({ number: n, top: (n - 1) * fallbackSpacing })
      }
      return positions
    }

    const existingTops = Array.from(byNumber.values()).sort((a, b) => a - b)
    let lastTop = existingTops.length > 0 ? existingTops[existingTops.length - 1] : 0

    for (let n = 1; n <= sourcesCount; n++) {
      const top = byNumber.get(n)
      if (typeof top === "number") {
        positions.push({ number: n, top })
        continue
      }

      lastTop = lastTop + minSpacing
      positions.push({ number: n, top: lastTop })
    }

    positions.sort((a, b) => a.top - b.top)
    for (let i = 1; i < positions.length; i++) {
      const prev = positions[i - 1]
      const curr = positions[i]
      if (curr.top - prev.top < minSpacing) {
        curr.top = prev.top + minSpacing
      }
    }

    return positions
  }, [citationPositions, sourcesForRender])

  const orderedSources = useMemo(() => {
    if (!sourcesForRender || sourcesForRender.length === 0) return []

    const seen = new Set<string>()
    const appearanceOrder = computedBadgePositions
      .slice()
      .sort((a, b) => a.top - b.top)
      .map((pos) => sourcesForRender[pos.number - 1])
      .filter(Boolean) as PDFSource[]

    const uniqueByAppearance: PDFSource[] = []
    appearanceOrder.forEach((src) => {
      const key = src.id || `${src.title}-${src.pageNumber}`
      if (seen.has(key)) return
      seen.add(key)
      uniqueByAppearance.push(src)
    })

    sourcesForRender.forEach((src) => {
      const key = src.id || `${src.title}-${src.pageNumber}`
      if (!seen.has(key)) {
        seen.add(key)
        uniqueByAppearance.push(src)
      }
    })

    return uniqueByAppearance.map((src) => {
      const idx = sourcesForRender.findIndex((item) => item.id === src.id)
      return { source: src, number: idx >= 0 ? idx + 1 : -1 }
    })
  }, [computedBadgePositions, sourcesForRender])

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="flex justify-start"
      >
        <div ref={messageRef} className="relative flex-1 max-w-[90%]">
          <div className="bg-black/60 backdrop-blur-md rounded-2xl px-6 py-4 border border-white/20 text-white" >
            {tocItems.length > 0 && (
              <div className="sticky top-2 z-10 flex justify-end mb-2">
                <button
                  type="button"
                  onClick={scrollToToc}
                  className="text-xs px-2.5 py-1 rounded-full border border-white/20 bg-black/60 text-gray-200 hover:text-white hover:border-white/40 transition-colors"
                >
                  回到目录
                </button>
              </div>
            )}
            {tocItems.length > 0 && (
              <div
                ref={tocRef}
                id={`${headingIdPrefix}-toc`}
                className="mb-4 rounded-lg border border-white/10 bg-white/5 px-4 py-3"
              >
                <div className="text-sm font-semibold text-white mb-2">目录</div>
                <ul className="space-y-1 text-sm text-gray-200">
                  {tocItems.map((item) => (
                    <li key={item.id} className={`flex items-start gap-2 ${getTocIndentClass(item.level)}`}>
                      <span
                        aria-hidden="true"
                        className="mt-1.5 h-1.5 w-1.5 rounded-full bg-blue-400 flex-shrink-0"
                      />
                      <button
                        type="button"
                        onClick={() => scrollToHeading(item.id)}
                        className="text-left text-blue-300 hover:text-blue-200 transition-colors leading-relaxed"
                      >
                        {item.text}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <MarkdownContent
              content={normalizedContent}
              images={images}
              sources={sourcesForRender}
              onCitationPositions={setCitationPositions}
              onCitationClick={handleCitationClick}
              positionAnchorRef={messageRef}
            />
          </div>

          {/* [FIX 2025-12-09] 移除右侧资料预览卡片（PDFSourceCard），只保留数字标记 */}
          {/* 原来的 PDFSourceCard 组件已被移除，用户只需要简单的数字引用标记 */}

          {sourcesForRender && sourcesForRender.length > 0 && (
            <div className="absolute top-0 left-full ml-2 pointer-events-auto z-50">
              {sourcesForRender.map((source, index) => {
                const position = computedBadgePositions.find(p => p.number === index + 1)
                if (!position) return null
                
                return (
                  <PDFCitationBadge
                    key={`${source.id}-${index}`}
                    source={source}
                    citationNumber={index + 1}
                    onClick={() => handlePDFClick(source)}
                    style={{
                      top: `${position.top}px`,
                    }}
                  />
                )
              })}
            </div>
          )}
        </div>
      </motion.div>

      {/* [FIX 2026-01-18] 参考资料列表 - 显示资料的详细信息和用途说明 */}
      {orderedSources.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.2 }}
          className="mt-4 flex justify-start"
        >
          <div className="w-full max-w-[90%]">
            <div className="bg-black/40 backdrop-blur-md rounded-xl px-5 py-4 border border-white/10">
              <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                <span className="text-blue-400">📚</span>
                参考资料
              </h3>
              <div className="space-y-3.5">
                {orderedSources.map(({ source, number }) => {
                  // 生成资料用途说明
                  const purposeDescription = generatePurposeDescription(source)

                  return (
                    <div
                      key={source.id}
                      className="flex items-start gap-4 text-sm text-gray-300 hover:bg-white/5 rounded-lg p-3 transition-colors cursor-pointer border border-white/5"
                      onClick={() => handlePDFClick(source)}
                    >
                      <span className="text-blue-400 font-semibold min-w-[2rem] text-base">[{number > 0 ? number : "·"}]</span>
                      <div className="flex-1 min-w-0 space-y-1.5">
                        {/* 资料标题和位置 */}
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <span className="font-medium text-white text-base">{source.title}</span>
                          <span className="text-xs text-gray-400 bg-white/5 px-2 py-0.5 rounded">
                            第 {source.pageNumber} 页
                          </span>
                          {source.section && (
                            <span className="text-xs text-gray-500">· {source.section}</span>
                          )}
                        </div>

                        {/* 资料用途说明 */}
                        {purposeDescription && (
                          <div className="text-xs text-yellow-200/80 bg-yellow-500/10 px-2 py-1 rounded border border-yellow-500/20">
                            💡 {purposeDescription}
                          </div>
                        )}

                        {/* 资料摘要 */}
                        {source.snippet && (
                          <div className="text-xs text-gray-400 leading-relaxed line-clamp-2 pl-2 border-l-2 border-gray-700">
                            {source.snippet}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        </motion.div>
      )}

      <PDFViewerModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        source={selectedPDF}
      />
    </>
  )
}

// 生成资料用途说明
function generatePurposeDescription(source: PDFSource): string {
  const title = String(source.title || "")
  const section = String(source.section || "")
  const snippet = String(source.snippet || "")
  const contentType = source.contentType || "text"

  const text = `${title} ${section} ${snippet}`.toLowerCase()

  // 根据内容类型和关键词生成用途说明
  if (contentType === "image") {
    if (/(平面|布局|布置)/.test(text) && /(详图|节点|构造|大样|设备)/.test(text)) {
      return "提供平面布置与关键节点示意，用于整体到细部的配置校对"
    }
    if (/(平面|布局|布置)/.test(text)) {
      return "展示空间平面布局与流线关系，帮助理解功能分区与尺度"
    }
    if (/(详图|节点|构造|大样|设备)/.test(text)) {
      return "提供节点/设备安装示意，用于深化设计与施工落地"
    }
    return "包含关键配图，用于核对空间布置与设备点位关系"
  }

  // 规范标准类
  if (/(规范|标准|要求|应当|必须|不得|条文|指标)/.test(text)) {
    return "提供规范条文与技术指标，用于合规性对照与参数校核"
  }

  // 流程流线类
  if (/(流程|流线|洁污|人流|物流|无菌|污染|感控|分区)/.test(text)) {
    return "阐述功能流程与感染控制要点，用于流线与分区策略设计"
  }

  // 空间布局类
  if (/(平面|布局|布置|尺度|面积|配置)/.test(text)) {
    return "说明空间配置与尺度要求，用于方案阶段的快速对照"
  }

  // 设计建议类
  if (/(建议|推荐|优化|改进|创新|案例)/.test(text)) {
    return "提供设计建议与优化方向，用于方案改进与创新参考"
  }

  // 对比分析类
  if (/(对比|比较|区别|优缺点|特点)/.test(text)) {
    return "对比不同方案的特点与适用场景，用于方案选型决策"
  }

  // 默认说明
  return "提供相关背景信息与技术细节，用于深入理解问题"
}
