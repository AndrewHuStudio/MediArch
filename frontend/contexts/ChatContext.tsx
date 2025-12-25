"use client"

import type React from "react"
import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import type { PDFSource } from "@/components/chat/pdf-source-card"

// 消息类型定义
export interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  files?: File[]
  agentThinking?: AgentThinkingStep[]
  sources?: PDFSource[]
  images?: string[]
}

export interface AgentThinkingStep {
  agentName: string
  thoughts: string[]
  isActive: boolean
  isComplete: boolean
}

// 对话数据结构
export interface StoredConversation {
  id: string
  title: string
  summary?: string
  messages: Message[]
  timestamp: Date
  isPinned: boolean
}

// GraphData 类型定义
export interface GraphData {
  nodes: Array<{ id: string; label: string; type: string }>
  links: Array<{ source: string; target: string; label: string }>
}

// Context 状态类型
interface ChatContextState {
  // 消息相关
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  addMessage: (message: Message) => void
  clearMessages: () => void

  // 加载状态
  isLoading: boolean
  setIsLoading: (loading: boolean) => void
  streamingMessage: string
  setStreamingMessage: (message: string) => void

  // Agent 状态
  isThinking: boolean
  setIsThinking: (thinking: boolean) => void
  activeAgentIndex: number
  setActiveAgentIndex: (index: number) => void
  currentThought: string
  setCurrentThought: (thought: string) => void
  agentStatus: "thinking" | "synthesizing" | "idle"
  setAgentStatus: (status: "thinking" | "synthesizing" | "idle") => void

  // 对话元数据
  conversationTitle: string
  setConversationTitle: (title: string) => void
  conversationSummary: string
  setConversationSummary: (summary: string) => void
  isConversationPinned: boolean
  setIsConversationPinned: (pinned: boolean) => void
  isAutoTitleActive: boolean
  setIsAutoTitleActive: (active: boolean) => void
  currentConversationId: string | undefined
  setCurrentConversationId: (id: string | undefined) => void

  // 知识图谱
  graphData: GraphData
  setGraphData: React.Dispatch<React.SetStateAction<GraphData>>
  isGraphAnimating: boolean
  setIsGraphAnimating: (animating: boolean) => void

  // UI 状态
  showSuggestedQuestions: boolean
  setShowSuggestedQuestions: (show: boolean) => void
  isInitialState: boolean
  setIsInitialState: (initial: boolean) => void
}

const ChatContext = createContext<ChatContextState | null>(null)

// 自定义 Hook
export function useChatContext() {
  const context = useContext(ChatContext)
  if (!context) {
    throw new Error("useChatContext must be used within ChatProvider")
  }
  return context
}

// Provider 组件
export function ChatProvider({ children }: { children: ReactNode }) {
  // 消息相关状态
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [streamingMessage, setStreamingMessage] = useState("")

  // Agent 状态
  const [isThinking, setIsThinking] = useState(false)
  const [activeAgentIndex, setActiveAgentIndex] = useState(-1)
  const [currentThought, setCurrentThought] = useState("")
  const [agentStatus, setAgentStatus] = useState<"thinking" | "synthesizing" | "idle">("idle")

  // 对话元数据
  const [conversationTitle, setConversationTitle] = useState("新的对话")
  const [conversationSummary, setConversationSummary] = useState("大模型正在梳理对话意图...")
  const [isConversationPinned, setIsConversationPinned] = useState(false)
  const [isAutoTitleActive, setIsAutoTitleActive] = useState(true)
  const [currentConversationId, setCurrentConversationId] = useState<string | undefined>(undefined)

  // 知识图谱
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] })
  const [isGraphAnimating, setIsGraphAnimating] = useState(false)

  // UI 状态
  const [showSuggestedQuestions, setShowSuggestedQuestions] = useState(false)
  const [isInitialState, setIsInitialState] = useState(true)

  // 辅助方法
  const addMessage = useCallback((message: Message) => {
    setMessages((prev) => [...prev, message])
  }, [])

  const clearMessages = useCallback(() => {
    setMessages([])
  }, [])

  const value: ChatContextState = {
    // 消息相关
    messages,
    setMessages,
    addMessage,
    clearMessages,

    // 加载状态
    isLoading,
    setIsLoading,
    streamingMessage,
    setStreamingMessage,

    // Agent 状态
    isThinking,
    setIsThinking,
    activeAgentIndex,
    setActiveAgentIndex,
    currentThought,
    setCurrentThought,
    agentStatus,
    setAgentStatus,

    // 对话元数据
    conversationTitle,
    setConversationTitle,
    conversationSummary,
    setConversationSummary,
    isConversationPinned,
    setIsConversationPinned,
    isAutoTitleActive,
    setIsAutoTitleActive,
    currentConversationId,
    setCurrentConversationId,

    // 知识图谱
    graphData,
    setGraphData,
    isGraphAnimating,
    setIsGraphAnimating,

    // UI 状态
    showSuggestedQuestions,
    setShowSuggestedQuestions,
    isInitialState,
    setIsInitialState,
  }

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}
