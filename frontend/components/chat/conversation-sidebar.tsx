"use client"

import { useState, useEffect } from "react"
import { MessageSquarePlus, Trash2, ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"
import { usePageTransition } from "@/components/page-transition"

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
const getAllConversations = (): Conversation[] => {
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
              title: parsed.title || "新的对话",
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
  const { startTransition } = usePageTransition()
  const [conversations, setConversations] = useState<Conversation[]>([])

  // 从 localStorage 加载对话历史
  const loadConversations = () => {
    const allConversations = getAllConversations()
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
  }, [])

  // 删除对话
  const handleDeleteConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()

    const shouldDelete = window.confirm("确定要删除这条对话吗？\n删除后内容将无法恢复。")
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
    const now = new Date()
    const diff = now.getTime() - date.getTime()
    const minutes = Math.floor(diff / (1000 * 60))
    const hours = Math.floor(diff / (1000 * 60 * 60))
    const days = Math.floor(diff / (1000 * 60 * 60 * 24))

    if (minutes < 1) return "刚刚"
    if (minutes < 60) return `${minutes}分钟前`
    if (hours < 24) return `${hours}小时前`
    if (days < 7) return `${days}天前`
    return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" })
  }

  const handleGoHome = () => {
    startTransition("/")
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
        "h-full bg-black/40 backdrop-blur-md border-r border-white/10",
        "flex flex-col overflow-hidden flex-shrink-0",
      )}
    >
      {/* Header with New Conversation Button */}
      <div className="p-3 border-b border-white/10 flex-shrink-0">
        <Button
          onClick={handleGoHome}
          className={cn(
            "w-full bg-white/10 hover:bg-white/20 text-white border border-white/20",
            "transition-all duration-200 mb-2",
            isCollapsed ? "px-0 justify-center" : "justify-start gap-2",
          )}
        >
          <ArrowLeft className="w-4 h-4 flex-shrink-0" />
          {!isCollapsed && <span className="text-sm">返回首页</span>}
        </Button>

        <Button
          onClick={onNewConversation}
          className={cn(
            "w-full bg-white/10 hover:bg-white/20 text-white border border-white/20",
            "transition-all duration-200",
            isCollapsed ? "px-0 justify-center" : "justify-start gap-2",
          )}
        >
          <MessageSquarePlus className="w-4 h-4 flex-shrink-0" />
          {!isCollapsed && <span className="text-sm">新建对话</span>}
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
              <div className="px-2 py-1 text-xs text-gray-400 font-medium">历史对话</div>
              {conversations.length === 0 ? (
                <div className="px-2 py-8 text-center">
                  <p className="text-xs text-gray-500">暂无对话记录</p>
                  <p className="text-xs text-gray-600 mt-1">点击上方创建新对话</p>
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
                      "hover:bg-white/10 group relative",
                      currentConversationId === conversation.id
                        ? "bg-white/15 border border-white/20 ring-1 ring-white/10"
                        : "bg-white/5 border border-transparent",
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
                            <span className="text-yellow-400 text-xs">📌</span>
                          )}
                          <h4 className="text-sm font-medium text-white truncate flex-1">
                            {conversation.title}
                          </h4>
                        </div>
                        {conversation.preview && (
                          <p className="text-xs text-gray-400 truncate mt-1">{conversation.preview}</p>
                        )}
                        <p className="text-xs text-gray-500 mt-1">{formatTimestamp(conversation.timestamp)}</p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="opacity-0 group-hover:opacity-100 transition-opacity h-6 w-6 text-gray-400 hover:text-red-400 hover:bg-red-500/10 flex-shrink-0"
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
