"use client"

import { useState, useRef, useEffect } from "react"
import { motion } from "framer-motion"
import { Bot } from 'lucide-react'
import { PDFCitationBadge, type PDFSource } from "./pdf-citation-badge"
import { PDFSourceCard } from "./pdf-source-card"
import { PDFViewerModal } from "./pdf-viewer-modal"
import { ImageLightbox } from "@/components/ui/image-lightbox"

interface MarkdownContentProps {
  content: string
  images?: string[]
  sources?: PDFSource[]
  onCitationPositions?: (positions: Array<{ number: number; top: number }>) => void
}

function MarkdownContent({ content, images, sources, onCitationPositions }: MarkdownContentProps) {
  const contentRef = useRef<HTMLDivElement>(null)
  const citationRefs = useRef<Map<number, HTMLSpanElement>>(new Map())

  useEffect(() => {
    if (!sources || sources.length === 0 || !onCitationPositions) return

    const updatePositions = () => {
      const positions: Array<{ number: number; top: number }> = []
      const seenCitations = new Set<number>()
      
      citationRefs.current.forEach((element, citationNumber) => {
        if (element && contentRef.current && !seenCitations.has(citationNumber)) {
          const rect = element.getBoundingClientRect()
          const containerRect = contentRef.current.getBoundingClientRect()
          const relativeTop = rect.top - containerRect.top
          positions.push({ number: citationNumber, top: relativeTop })
          seenCitations.add(citationNumber)
        }
      })

      positions.sort((a, b) => a.number - b.number)
      onCitationPositions(positions)
    }

    requestAnimationFrame(updatePositions)
  }, [content, sources, onCitationPositions])

  const parseContentWithCitationsAndImages = (text: string) => {
    const parts: React.ReactNode[] = []
    const regex = /\[(\d+)\]|\[image:(\d+)\]/g
    let lastIndex = 0
    let match
    let keyIndex = 0
    const firstOccurrence = new Set<number>()

    while ((match = regex.exec(text)) !== null) {
      if (lastIndex < match.index) {
        const textBefore = text.slice(lastIndex, match.index)
        parts.push(
          <span key={`text-${keyIndex++}`}>
            {parseMarkdownText(textBefore)}
          </span>
        )
      }

      if (match[1]) {
        const citationNum = parseInt(match[1])
        const isFirst = !firstOccurrence.has(citationNum)
        if (isFirst) firstOccurrence.add(citationNum)
        
        parts.push(
          <span
            key={`cite-${citationNum}-${match.index}`}
            ref={(el) => {
              if (el && isFirst) citationRefs.current.set(citationNum, el)
            }}
            className="inline-flex items-center align-baseline mx-0.5"
          >
            <sup className="text-blue-400 text-xs font-medium cursor-pointer hover:text-blue-300 transition-colors">
              [{citationNum}]
            </sup>
          </span>
        )
      } else if (match[2]) {
        const imgIdx = parseInt(match[2])
        if (images && images[imgIdx]) {
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
                alt={`图 ${imgIdx + 1}: 来自数据库的相关资料`}
              />
            </motion.div>
          )
        }
      }

      lastIndex = match.index + match[0].length
      keyIndex++
    }

    if (lastIndex < text.length) {
      const remainingText = text.slice(lastIndex)
      parts.push(
        <span key="text-final">
          {parseMarkdownText(remainingText)}
        </span>
      )
    }

    return parts.length > 0 ? parts : parseMarkdownText(text)
  }

  const parseMarkdownText = (text: string) => {
    const parts: React.ReactNode[] = []
    const codeBlockRegex = /\`\`\`(\w+)?\n([\s\S]*?)\`\`\`/g
    let lastIndex = 0
    let match

    const codeBlocks: Array<{ start: number; end: number; lang: string; code: string }> = []
    while ((match = codeBlockRegex.exec(text)) !== null) {
      codeBlocks.push({
        start: match.index,
        end: match.index + match[0].length,
        lang: match[1] || "text",
        code: match[2].trim(),
      })
    }

    codeBlocks.forEach((block, idx) => {
      if (lastIndex < block.start) {
        const textBefore = text.slice(lastIndex, block.start)
        parts.push(<span key={`text-${idx}`} dangerouslySetInnerHTML={{ __html: formatText(textBefore) }} />)
      }

      parts.push(
        <div key={`code-${idx}`} className="my-3 rounded-lg overflow-hidden bg-gray-900 border border-gray-700">
          <div className="flex items-center justify-between px-4 py-2 bg-gray-800/50 border-b border-gray-700">
            <span className="text-xs text-gray-400 font-mono">{block.lang}</span>
          </div>
          <pre className="p-4 overflow-x-auto">
            <code className="text-sm font-mono text-gray-100">{block.code}</code>
          </pre>
        </div>,
      )

      lastIndex = block.end
    })

    if (lastIndex < text.length) {
      const remainingText = text.slice(lastIndex)
      parts.push(<span key="text-final" dangerouslySetInnerHTML={{ __html: formatText(remainingText) }} />)
    }

    return parts.length > 0 ? parts : <span dangerouslySetInnerHTML={{ __html: formatText(text) }} />
  }

  const formatText = (text: string) => {
    // 处理标题
    let result = text
      .replace(/^#### (.+)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1.5 text-blue-300">$1</h4>')
      .replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold mt-4 mb-2 text-white">$1</h3>')
      .replace(/^## (.+)$/gm, '<h2 class="text-lg font-semibold mt-5 mb-2.5 text-white border-b border-white/10 pb-1">$1</h2>')
      .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold mt-6 mb-3 text-white">$1</h1>')

    // 处理粗体和斜体
    result = result
      .replace(/\*\*(.+?)\*\*/g, '<strong class="text-white font-semibold">$1</strong>')
      .replace(/\*(.+?)\*/g, '<em class="text-gray-300">$1</em>')

    // 处理行内代码
    result = result.replace(/`([^`]+)`/g, '<code class="bg-gray-700/50 px-1.5 py-0.5 rounded text-sm font-mono text-blue-300">$1</code>')

    // 处理无序列表 - 使用圆点符号，确保正确缩进
    result = result.replace(/^[\s]*[-•]\s+(.+)$/gm, '<div class="flex items-start gap-2 ml-2 my-1"><span class="text-blue-400 mt-1.5 text-xs">●</span><span class="flex-1">$1</span></div>')

    // 处理有序列表 - 移除多余的点号，保持数字清晰
    result = result.replace(/^[\s]*(\d+)[.、．]\s*(.+)$/gm, '<div class="flex items-start gap-2 ml-2 my-1"><span class="text-blue-400 font-medium min-w-[1.5rem]">$1.</span><span class="flex-1">$2</span></div>')

    // 处理引用块
    result = result.replace(/^>\s*(.+)$/gm, '<blockquote class="border-l-2 border-blue-500/50 pl-3 my-2 text-gray-300 italic">$1</blockquote>')

    // 处理分隔线
    result = result.replace(/^---+$/gm, '<hr class="border-white/10 my-4"/>')

    // 处理段落间距 - 双换行转为段落分隔
    result = result.replace(/\n\n+/g, '</p><p class="my-2">')

    // 处理单换行 - 在列表项之外保持换行
    result = result.replace(/([^>])\n([^<])/g, '$1<br/>$2')

    return result
  }

  return (
    <div ref={contentRef} className="prose prose-invert prose-sm max-w-none">
      {parseContentWithCitationsAndImages(content)}
    </div>
  )
}

export function MessageWithSources({ content, sources, images }: MarkdownContentProps & { sources?: PDFSource[] }) {
  const [selectedPDF, setSelectedPDF] = useState<PDFSource | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [citationPositions, setCitationPositions] = useState<Array<{ number: number; top: number }>>([])

  const handlePDFClick = (source: PDFSource) => {
    setSelectedPDF(source)
    setIsModalOpen(true)
  }

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="flex gap-4 justify-start"
      >
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center">
          <Bot className="w-5 h-5 text-white" />
        </div>

        <div className="relative flex-1 max-w-[65%]">
          <div className="bg-black/60 backdrop-blur-md rounded-2xl px-4 py-3 border border-white/20 text-white">
            <MarkdownContent
              content={content}
              images={images}
              sources={sources}
              onCitationPositions={setCitationPositions}
            />
          </div>

          {/* [FIX 2025-12-09] 移除右侧资料预览卡片（PDFSourceCard），只保留数字标记 */}
          {/* 原来的 PDFSourceCard 组件已被移除，用户只需要简单的数字引用标记 */}

          {sources && sources.length > 0 && citationPositions.length > 0 && (
            <div className="absolute top-0 left-full ml-2 pointer-events-none z-50">
              {sources.map((source, index) => {
                const position = citationPositions.find(p => p.number === index + 1)
                if (!position) return null
                
                return (
                  <PDFCitationBadge
                    key={source.id}
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

      <PDFViewerModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        source={selectedPDF}
      />
    </>
  )
}
