"use client"

import { useId, useMemo, useRef } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { FileText, ImageIcon } from "lucide-react"
import { useChatContext } from "@/contexts/ChatContext"
import { ShiningText } from "@/components/ui/shining-text"
import { MarkdownContent, MessageWithSources } from "@/components/chat/message-with-sources"
import { buildHeadingIdPrefix, prepareMarkdownWithToc } from "@/lib/markdown-toc"

function AssistantMessageContent({ content, images }: { content: string; images: string[] }) {
  const tocRef = useRef<HTMLDivElement>(null)
  const tocId = useId()
  const headingIdPrefix = useMemo(() => buildHeadingIdPrefix(tocId, "toc"), [tocId])
  const { content: contentWithToc, tocItems } = useMemo(
    () => prepareMarkdownWithToc(content, headingIdPrefix),
    [content, headingIdPrefix]
  )

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

  return (
    <>
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
      <MarkdownContent content={contentWithToc} images={images} />
    </>
  )
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
    activeAgents,
  } = useChatContext()

  const resolvedAgentIndex = useMemo(() => {
    if (activeAgentIndex >= 0) return activeAgentIndex
    if (activeAgents.size === 0) return -1
    return Math.min(...Array.from(activeAgents))
  }, [activeAgentIndex, activeAgents])

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
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[60%] rounded-2xl px-4 py-3 ${
                msg.role === "user"
                  ? "bg-white/90 backdrop-blur-sm text-black"
                  : "bg-black/60 backdrop-blur-md text-white border border-white/20"
              }`}
            >
              {msg.role === "assistant" ? (
                <AssistantMessageContent content={msg.content} images={msg.images || []} />
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
          </motion.div>
        )
      )}

      {/* Agent 思考状态 */}
      <AnimatePresence>
        {isLoading && isThinking && !streamingMessage && (
          <motion.div
            key="agent-thinking"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.25 }}
            className="flex justify-start"
          >
            <div className="max-w-[75%] bg-black/60 backdrop-blur-md rounded-2xl px-4 py-3 border border-white/20 text-white">
              <div className="mb-2">
                <ShiningText text={`${resolvedAgentIndex >= 0 ? agents[resolvedAgentIndex] : "系统"} 正在思考...`} />
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
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex justify-start">
          <div className="max-w-[75%] bg-black/60 backdrop-blur-md rounded-2xl px-4 py-3 border border-white/20 text-white">
            <AssistantMessageContent content={streamingMessage} images={[]} />
          </div>
        </motion.div>
      )}

      {/* 加载动画 */}
      {isLoading && !streamingMessage && !isThinking && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex justify-start">
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
