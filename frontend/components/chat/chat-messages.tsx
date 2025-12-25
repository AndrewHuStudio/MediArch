"use client"

import type React from "react"
import { motion, AnimatePresence } from "framer-motion"
import { User, Bot, FileText, ImageIcon } from "lucide-react"
import { useChatContext } from "@/contexts/ChatContext"
import { ShiningText } from "@/components/ui/shining-text"
import { MessageWithSources } from "@/components/chat/message-with-sources"
import dynamic from "next/dynamic"

// 动态导入 ImageLightbox
const ImageLightbox = dynamic(() => import("@/components/ui/image-lightbox"), {
  ssr: false,
})

// Markdown 内容渲染组件
function MarkdownContent({ content, images }: { content: string; images: string[] }) {
  const parseMarkdown = (text: string) => {
    const parts: React.ReactNode[] = []
    const codeBlockRegex = /\`\`\`(\w+)?\n([\s\S]*?)\`\`\`/g
    let currentIndex = 0
    const codeBlocks: Array<{ start: number; end: number; lang: string; code: string }> = []
    let match

    while ((match = codeBlockRegex.exec(text)) !== null) {
      codeBlocks.push({
        start: match.index,
        end: match.index + match[0].length,
        lang: match[1] || "text",
        code: match[2].trim(),
      })
    }

    const processedParts: React.ReactNode[] = []

    codeBlocks.forEach((block, idx) => {
      if (currentIndex < block.start) {
        const textBefore = text.slice(currentIndex, block.start)
        const textParts = processTextWithImages(textBefore, images)
        textParts.forEach((part, partIdx) => {
          processedParts.push(<span key={`text-${idx}-${partIdx}`}>{part}</span>)
        })
      }

      processedParts.push(
        <div key={`code-${idx}`} className="my-3 rounded-lg overflow-hidden bg-gray-900 border border-gray-700">
          <div className="flex items-center justify-between px-4 py-2 bg-gray-800/50 border-b border-gray-700">
            <span className="text-xs text-gray-400 font-mono">{block.lang}</span>
          </div>
          <pre className="p-4 overflow-x-auto">
            <code className="text-sm font-mono text-gray-100">{block.code}</code>
          </pre>
        </div>,
      )

      currentIndex = block.end
    })

    if (currentIndex < text.length) {
      const remainingText = text.slice(currentIndex)
      const textParts = processTextWithImages(remainingText, images)
      textParts.forEach((part, partIdx) => {
        processedParts.push(<span key={`text-final-${partIdx}`}>{part}</span>)
      })
    }

    return processedParts
  }

  const processTextWithImages = (text: string, images: string[]) => {
    const parts: React.ReactNode[] = []
    const imageRegex = /\[image:(\d+)\]/g
    let lastIndex = 0
    let match

    while ((match = imageRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        const textBefore = text.slice(lastIndex, match.index)
        parts.push(<span key={`text-${lastIndex}`} dangerouslySetInnerHTML={{ __html: formatText(textBefore) }} />)
      }

      const imageIndex = parseInt(match[1])
      if (images[imageIndex]) {
        parts.push(
          <ImageLightbox
            key={`image-${imageIndex}-${match.index}`}
            src={images[imageIndex] || "/placeholder.svg"}
            alt={`图 ${imageIndex + 1}: 来自数据库的相关资料`}
          />
        )
      }

      lastIndex = match.index + match[0].length
    }

    if (lastIndex < text.length) {
      const remainingText = text.slice(lastIndex)
      parts.push(<span key={`text-${lastIndex}`} dangerouslySetInnerHTML={{ __html: formatText(remainingText) }} />)
    }

    return parts.length > 0 ? parts : [<span key="text-all" dangerouslySetInnerHTML={{ __html: formatText(text) }} />]
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

  return <div className="prose prose-invert prose-sm max-w-none">{parseMarkdown(content)}</div>
}

// 主消息列表组件
export function ChatMessages({ agents }: { agents: string[] }) {
  const {
    messages,
    isLoading,
    streamingMessage,
    isThinking,
    activeAgentIndex,
    currentThought,
  } = useChatContext()

  return (
    <div className="flex-1 overflow-y-auto px-2 space-y-6">
      {messages.map((msg, index) =>
        msg.role === "assistant" && (msg.sources || msg.images) ? (
          <MessageWithSources key={msg.id} content={msg.content} sources={msg.sources} images={msg.images} />
        ) : (
          <motion.div
            key={msg.id}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: index * 0.1 }}
            className={`flex gap-4 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.role === "assistant" && (
              <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center">
                <Bot className="w-5 h-5 text-white" />
              </div>
            )}

            <div
              className={`max-w-[60%] rounded-2xl px-4 py-3 ${
                msg.role === "user"
                  ? "bg-white/90 backdrop-blur-sm text-black"
                  : "bg-black/60 backdrop-blur-md text-white border border-white/20"
              }`}
            >
              {msg.role === "assistant" ? (
                <MarkdownContent content={msg.content} images={msg.images || []} />
              ) : (
                <>
                  <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                  {msg.files && msg.files.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {msg.files.map((file, idx) => (
                        <div key={idx} className="flex items-center gap-2 bg-gray-100 rounded-lg px-3 py-1.5 text-xs">
                          {file.type.startsWith("image/") ? (
                            <ImageIcon className="w-3 h-3" />
                          ) : (
                            <FileText className="w-3 h-3" />
                          )}
                          <span className="truncate max-w-[150px]">{file.name}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            {msg.role === "user" && (
              <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white text-black flex items-center justify-center">
                <User className="w-5 h-5" />
              </div>
            )}
          </motion.div>
        )
      )}

      {/* Agent 思考状态 */}
      <AnimatePresence>
        {isLoading && isThinking && activeAgentIndex >= 0 && !streamingMessage && (
          <motion.div
            key="agent-thinking"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.25 }}
            className="flex gap-4 justify-start"
          >
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div className="max-w-[75%] bg-black/60 backdrop-blur-md rounded-2xl px-4 py-3 border border-white/20 text-white">
            <div className="mb-2">
              <ShiningText text={`${agents[activeAgentIndex]} 正在思考...`} />
            </div>
            <div className="flex items-start gap-2 text-sm">
              <span className="text-gray-400">[思路]</span>
              <AnimatePresence mode="wait">
                <motion.span
                  key={currentThought}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.3 }}
                  className="text-gray-300"
                >
                  {currentThought}
                </motion.span>
              </AnimatePresence>
            </div>
          </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 流式消息 */}
      {isLoading && streamingMessage && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex gap-4 justify-start">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div className="max-w-[75%] bg-black/60 backdrop-blur-md rounded-2xl px-4 py-3 border border-white/20 text-white">
            <MarkdownContent content={streamingMessage} images={[]} />
          </div>
        </motion.div>
      )}

      {/* 加载动画 */}
      {isLoading && !streamingMessage && !isThinking && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex gap-4 justify-start">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div className="bg-black/60 backdrop-blur-md rounded-2xl px-4 py-3 border border-white/20">
            <div className="flex gap-1">
              <div className="w-2 h-2 rounded-full bg-white/60 animate-bounce" style={{ animationDelay: "0ms" }} />
              <div className="w-2 h-2 rounded-full bg-white/60 animate-bounce" style={{ animationDelay: "150ms" }} />
              <div className="w-2 h-2 rounded-full bg-white/60 animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          </div>
        </motion.div>
      )}
    </div>
  )
}
