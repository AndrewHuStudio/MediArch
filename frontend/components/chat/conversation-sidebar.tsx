"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { MessageSquarePlus, Trash2, ArrowLeft, Pin } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"
import { useT } from "@/lib/i18n"
import { formatConversationTimestamp } from "@/lib/i18n/ui-copy"

interface Conversation {
  id: string
  title: string
  timestamp: Date
  preview?: string
  isPinned: boolean
}

interface ConversationSidebarProps {
  isCollapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void
  onNewConversation: () => void
  currentConversationId?: string
  onConversationSelect?: (id: string) => void
}

// 使用与 ChatInterface 相同的 localStorage 前缀
const CONVERSATION_STORAGE_PREFIX = "mediarch-conversation-"

// 辅助函数：从 localStorage 获取所有对话（只显示有消息的对话）
const getAllConversations = (defaultTitle: string): Conversation[] => {
  const conversations: Conversation[] = []

  try {
    // 遍历所有 localStorage 键
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith(CONVERSATION_STORAGE_PREFIX)) {
        const data = localStorage.getItem(key)
        if (data) {
          const parsed = JSON.parse(data)

          // 【关键修改】只显示有消息的对话，过滤掉空对话
          if (parsed.messages && parsed.messages.length > 0) {
            conversations.push({
              id: parsed.id,
              title: parsed.title || defaultTitle,
              timestamp: new Date(parsed.timestamp),
              preview: parsed.summary || parsed.messages?.[0]?.content?.slice(0, 50),
              isPinned: parsed.isPinned || false,
            })
          }
        }
      }
    }
  } catch (error) {
    console.error("Failed to load conversations:", error)
  }

  // 按时间戳排序：固定的在前，然后按时间倒序
  return conversations.sort((a, b) => {
    if (a.isPinned && !b.isPinned) return -1
    if (!a.isPinned && b.isPinned) return 1
    return b.timestamp.getTime() - a.timestamp.getTime()
  })
}

// 辅助函数：删除对话
const deleteConversationFromStorage = (id: string) => {
  try {
    const key = `${CONVERSATION_STORAGE_PREFIX}${id}`
    localStorage.removeItem(key)
  } catch (error) {
    console.error("Failed to delete conversation:", error)
  }
}

export function ConversationSidebar({
  isCollapsed,
  onCollapsedChange,
  onNewConversation,
  currentConversationId,
  onConversationSelect,
}: ConversationSidebarProps) {
  const router = useRouter()
  const { locale, t } = useT()
  const [conversations, setConversations] = useState<Conversation[]>([])

  useEffect(() => {
    void router.prefetch("/")
  }, [router])

  // 从 localStorage 加载对话历史
  const loadConversations = () => {
    const allConversations = getAllConversations(t('chat.defaultTitle'))
    setConversations(allConversations)
  }

  useEffect(() => {
    loadConversations()

    // 监听 storage 事件，当其他标签页更新时同步
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key && e.key.startsWith(CONVERSATION_STORAGE_PREFIX)) {
        loadConversations()
      }
    }

    window.addEventListener("storage", handleStorageChange)

    // 定期刷新对话列表（每3秒）
    const interval = setInterval(loadConversations, 3000)

    return () => {
      window.removeEventListener("storage", handleStorageChange)
      clearInterval(interval)
    }
  }, [t])

  // 删除对话
  const handleDeleteConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()

    const shouldDelete = window.confirm(t('chat.deleteConfirm'))
    if (!shouldDelete) return

    deleteConversationFromStorage(id)

    // 如果删除的是当前对话，创建新对话
    if (id === currentConversationId) {
      onNewConversation()
    } else {
      loadConversations()
    }
  }

  // 选择对话
  const handleSelectConversation = (id: string) => {
    if (onConversationSelect) {
      console.log("Selecting conversation:", id)
      onConversationSelect(id)
    }
  }

  const formatTimestamp = (date: Date) => {
    return formatConversationTimestamp(date, locale, t)
  }

  return (
    <motion.div
      onMouseEnter={() => onCollapsedChange(false)}
      onMouseLeave={() => onCollapsedChange(true)}
      initial={false}
      animate={{
        width: isCollapsed ? "4rem" : "16rem",
      }}
      transition={{ duration: 0.3, ease: "easeInOut" }}
      className={cn(
        "h-full bg-white/72 backdrop-blur-md border-r border-[#d9e7eb]",
        "flex flex-col overflow-hidden flex-shrink-0",
      )}
    >
      {/* Header with New Conversation Button */}
      <div className="p-3 border-b border-[#d9e7eb] flex-shrink-0">
        <Button
          asChild
          className={cn(
            "w-full bg-white/80 hover:bg-[#e6f4f6] text-[#0f4e63] border border-[#cfe2e7]",
            "transition-all duration-200 mb-2",
            isCollapsed ? "px-0 justify-center" : "justify-start gap-2",
          )}
        >
          <Link
            href="/"
            prefetch
            onMouseEnter={() => router.prefetch("/")}
            onFocus={() => router.prefetch("/")}
          >
            <ArrowLeft className="w-4 h-4 flex-shrink-0" />
            {!isCollapsed && <span className="text-sm">{t('chat.goHome')}</span>}
          </Link>
        </Button>

        <Button
          onClick={onNewConversation}
          className={cn(
            "w-full bg-[#0e7490] hover:bg-[#0f4e63] text-white border border-[#0e7490]",
            "transition-all duration-200",
            isCollapsed ? "px-0 justify-center" : "justify-start gap-2",
          )}
        >
          <MessageSquarePlus className="w-4 h-4 flex-shrink-0" />
          {!isCollapsed && <span className="text-sm">{t('chat.newConversation')}</span>}
        </Button>
      </div>

      {/* Conversation History */}
      <div className="flex-1 overflow-y-auto p-2">
        <AnimatePresence mode="wait">
          {!isCollapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="space-y-1"
            >
              <div className="px-2 py-1 text-xs text-[#6c858c] font-medium">{t('chat.history')}</div>
              {conversations.length === 0 ? (
                <div className="px-2 py-8 text-center">
                  <p className="text-xs text-[#6c858c]">{t('chat.noHistory')}</p>
                  <p className="text-xs text-[#8ba4ad] mt-1">{t('chat.noHistoryHint')}</p>
                </div>
              ) : (
                conversations.map((conversation) => (
                  <motion.div
                    key={conversation.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -20 }}
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleSelectConversation(conversation.id)}
                    className={cn(
                      "w-full text-left p-3 rounded-lg transition-all duration-200 cursor-pointer",
                      "hover:bg-[#e6f4f6] group relative",
                      currentConversationId === conversation.id
                        ? "bg-[#e6f4f6] border border-[#0e7490]/30 ring-1 ring-[#0e7490]/10"
                        : "bg-white/60 border border-transparent",
                    )}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault()
                        handleSelectConversation(conversation.id)
                      }
                    }}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1">
                          {conversation.isPinned && (
                            <Pin className="h-3 w-3 text-[#0e7490]" />
                          )}
                          <h4 className="text-sm font-medium text-[#12323a] truncate flex-1">
                            {conversation.title}
                          </h4>
                        </div>
                        {conversation.preview && (
                          <p className="text-xs text-[#516b72] truncate mt-1">{conversation.preview}</p>
                        )}
                        <p className="text-xs text-[#8ba4ad] mt-1">{formatTimestamp(conversation.timestamp)}</p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="opacity-0 group-hover:opacity-100 transition-opacity h-6 w-6 text-[#6c858c] hover:text-red-600 hover:bg-red-500/10 flex-shrink-0"
                        onClick={(e) => handleDeleteConversation(conversation.id, e)}
                      >
                        <Trash2 className="w-3 h-3" />
                      </Button>
                    </div>
                  </motion.div>
                ))
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}
