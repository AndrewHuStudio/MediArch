"use client"

import type React from "react"
import { useState, useRef, useEffect, useCallback, startTransition, useMemo } from "react"
import { PanelRightClose, PanelRightOpen, Stethoscope, Building2, FileSearch, Lightbulb, AlertCircle } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { motion, AnimatePresence } from "framer-motion"
import { Component as EtherealShadow } from "@/components/ui/ethereal-shadow"
import { SuggestedQuestions } from "@/components/ui/suggested-questions"
import { ConversationSidebar } from "@/components/chat/conversation-sidebar"
import { ConversationTopBar } from "@/components/chat/conversation-top-bar"
import type { PDFSource } from "@/components/chat/pdf-source-card"
import dynamic from "next/dynamic"
import type { GraphData } from "@/components/ui/knowledge-graph-d3"
import { ChatProvider, useChatContext } from "@/contexts/ChatContext"
import { ChatMessages } from "@/components/chat/chat-messages"
import { ChatInput } from "@/components/chat/chat-input"
import { initializeDemoConversations } from "@/lib/init-demo-conversations"

// API 客户端
import { chatApi, type StreamCallbacks, getApiUrl } from "@/lib/api"
import type { Citation, KnowledgeGraphData, AgentStatusUpdate } from "@/lib/api/types"

// 延迟加载非关键组件 - 性能优化
const AgentThinkingPanel = dynamic(() => import("@/components/chat/agent-thinking-panel"), {
  ssr: false,
  loading: () => <div className="h-full animate-pulse rounded-lg border border-white/10 bg-black/40 p-4 backdrop-blur-md" />,
})

const KnowledgeGraphPanel = dynamic(() => import("@/components/chat/knowledge-graph-panel"), {
  ssr: false,
  loading: () => <div className="h-full animate-pulse rounded-lg border border-white/10 bg-black/40 p-4 backdrop-blur-md" />,
})

// localStorage 相关常量
const CONVERSATION_STORAGE_PREFIX = "mediarch-conversation-"
const CURRENT_CONVERSATION_KEY = "mediarch-current-conversation-id"
const DEFAULT_CONVERSATION_TITLE = "新的对话"
const DEFAULT_CONVERSATION_SUMMARY = "大模型正在梳理对话意图..."

// 是否使用后端 API（可以通过环境变量控制）
const USE_BACKEND_API = process.env.NEXT_PUBLIC_USE_BACKEND_API !== "false"

// 对话数据结构
interface StoredConversation {
  id: string
  title: string
  summary?: string
  messages: Array<{
    id: string
    role: "user" | "assistant"
    content: string
    timestamp: Date
    files?: File[]
    sources?: PDFSource[]
    images?: string[]
  }>
  timestamp: Date
  isPinned: boolean
}

// localStorage 工具函数
const saveConversationToStorage = (conversation: StoredConversation) => {
  try {
    const key = `${CONVERSATION_STORAGE_PREFIX}${conversation.id}`
    localStorage.setItem(
      key,
      JSON.stringify({
        ...conversation,
        timestamp: conversation.timestamp.toISOString(),
      }),
    )
  } catch (error) {
    console.error("Failed to save conversation:", error)
  }
}

const loadConversationFromStorage = (id: string): StoredConversation | null => {
  try {
    const key = `${CONVERSATION_STORAGE_PREFIX}${id}`
    const data = localStorage.getItem(key)
    if (!data) return null

    const parsed = JSON.parse(data)
    return {
      ...parsed,
      timestamp: new Date(parsed.timestamp),
      messages: parsed.messages.map((msg: any) => ({
        ...msg,
        timestamp: new Date(msg.timestamp),
      })),
    }
  } catch (error) {
    console.error("Failed to load conversation:", error)
    return null
  }
}

const deleteConversationFromStorage = (id: string) => {
  try {
    const key = `${CONVERSATION_STORAGE_PREFIX}${id}`
    localStorage.removeItem(key)
  } catch (error) {
    console.error("Failed to delete conversation:", error)
  }
}

const setCurrentConversationIdStorage = (id: string | null) => {
  try {
    if (id) {
      localStorage.setItem(CURRENT_CONVERSATION_KEY, id)
    } else {
      localStorage.removeItem(CURRENT_CONVERSATION_KEY)
    }
  } catch (error) {
    console.error("Failed to set current conversation:", error)
  }
}

const getCurrentConversationIdStorage = (): string | null => {
  try {
    return localStorage.getItem(CURRENT_CONVERSATION_KEY)
  } catch (error) {
    console.error("Failed to get current conversation:", error)
    return null
  }
}

const createAutoConversationTitle = (raw: string, maxLength = 20) => {
  const normalized = raw.replace(/\s+/g, " ").trim()
  if (!normalized) return DEFAULT_CONVERSATION_TITLE
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized
}

const createConversationSummary = (raw: string) => {
  const normalized = raw.replace(/\s+/g, " ").trim()
  if (!normalized) return DEFAULT_CONVERSATION_SUMMARY
  return normalized.length > 60 ? `${normalized.slice(0, 60)}...` : normalized
}

const buildPdfUrl = (rawPdfPath?: string, documentPath?: string, filePath?: string) => {
  const toApiUrl = (path: string) => getApiUrl(path.startsWith("/") ? path : `/${path}`)

  const normalizeRelativePath = (path: string) => {
    // 去掉重复的 /api/v1 前缀，统一用 getApiUrl 拼接
    if (path.startsWith("/api/v1/")) return path.replace(/^\/api\/v1/, "")
    return path
  }

  const resolvePath = (path?: string) => {
    if (!path) return undefined
    const normalized = normalizeRelativePath(path)
    const isAbsolute = /^https?:\/\//i.test(normalized)
    if (isAbsolute) {
      return normalized
    }
    return toApiUrl(normalized)
  }

  // 1) 优先使用后端返回的 pdf_url
  const fromPdfUrl = resolvePath(rawPdfPath)
  if (fromPdfUrl) return fromPdfUrl

  // 2) 其次用 document_path / file_path 组装
  const fallbackPath = documentPath || filePath
  if (fallbackPath) {
    // 兼容绝对/相对路径，尽量取 documents 目录后的相对部分
    const sanitized = fallbackPath.replace(/\\/g, "/")
    const match = sanitized.match(/documents\/(.+)$/i)
    const relative = match ? match[1] : sanitized
    return toApiUrl(`/documents/pdf?path=${encodeURIComponent(relative)}`)
  }

  // 3) 无路径时返回 undefined，让上层走文本预览兜底
  return undefined
}

// 将后端引用格式转换为前端 PDFSource 格式
const citationsToPDFSources = (citations: Citation[]): PDFSource[] => {
  return citations.map((cite, index) => {
    const documentPath = cite.document_path || (cite as any).documentPath
    const filePath = cite.file_path || (cite as any).filePath
    const imageUrl = (cite as any).imageUrl || cite.image_url
    const rawPdfPath = cite.pdf_url || (cite as any).pdfUrl
    const pdfUrl = buildPdfUrl(rawPdfPath as string | undefined, documentPath, filePath)
    const normalizedPositions =
      Array.isArray(cite.positions) && cite.positions.length > 0
        ? cite.positions.map((pos: any) => {
            if (!pos) return null
            if (Array.isArray(pos.bbox)) {
              return { page: pos.page ?? cite.page_number ?? 1, bbox: pos.bbox as number[] }
            }
            // 兼容旧格式
            if (typeof pos.x === "number" && typeof pos.y === "number" && typeof pos.width === "number" && typeof pos.height === "number") {
              return {
                page: pos.page ?? cite.page_number ?? 1,
                bbox: [pos.x, pos.y, pos.x + pos.width, pos.y + pos.height],
              }
            }
            return null
          }).filter(Boolean)
        : undefined

    return {
      id: cite.chunk_id || `pdf-${index}`,
      title: cite.source,
      pageNumber: cite.page_number || 1,
      snippet: cite.snippet,
      highlightText: cite.highlight_text || cite.snippet,
      positions: normalizedPositions as PDFSource["positions"],
      pdfUrl,
      documentPath: cite.document_path,
      filePath: cite.file_path,
      imageUrl,
      thumbnail: imageUrl,
      section: cite.section || cite.sub_section,
      metadata: cite.metadata,
      docId: cite.doc_id,
      contentType: ((cite.content_type as any) || (cite.image_url ? "image" : undefined)) as any,
    }
  })
}

// 转换知识图谱数据格式
const convertKnowledgeGraphData = (data: any | null): GraphData => {
  if (!data) return { nodes: [], links: [] }

  // 处理可能的不同数据格式
  const rawNodes = data.nodes || []
  const rawLinks = data.links || data.edges || []

  // 去重节点（后端可能返回重复节点）
  const nodeMap = new Map<string, any>()
  for (const node of rawNodes) {
    const id = node.id || node.name || `node-${Math.random()}`
    if (!nodeMap.has(id)) {
      nodeMap.set(id, {
        id,
        label: node.label || node.name || node.id || "未知",
        type: mapNodeType(node.type),
      })
    }
  }

  // 过滤无效的边（source 或 target 不存在于节点中）
  const validLinks = rawLinks
    .map((link: any) => ({
      source: link.source,
      target: link.target,
      label: link.label || link.relation || "",
    }))
    .filter((link: any) => nodeMap.has(link.source) && nodeMap.has(link.target))

  return {
    nodes: Array.from(nodeMap.values()),
    links: validLinks,
  }
}

// 映射节点类型到前端支持的类型
function mapNodeType(type: string | undefined): string {
  if (!type) return "entity"

  // 直接使用 schema 定义的节点类型
  const schemaTypes = [
    "Hospital", "DepartmentGroup", "FunctionalZone", "Space",
    "DesignMethod", "DesignMethodCategory", "Case", "Source",
    "MedicalService", "MedicalEquipment", "TreatmentMethod",
    "KnowledgePoint"  // 新增：知识点类型
  ]

  if (schemaTypes.includes(type)) {
    return type
  }

  // 兼容旧的类型名称
  const typeLower = type.toLowerCase()

  if (typeLower.includes("hospital")) return "Hospital"
  if (typeLower.includes("department")) return "DepartmentGroup"
  if (typeLower.includes("zone") || typeLower.includes("功能分区")) return "FunctionalZone"
  if (typeLower.includes("space") || typeLower.includes("room")) return "Space"
  if (typeLower.includes("design") && typeLower.includes("method")) return "DesignMethod"
  if (typeLower.includes("case") || typeLower.includes("案例")) return "Case"
  if (typeLower.includes("knowledge") || typeLower.includes("知识")) return "KnowledgePoint"
  // 检查 document 或 doc 后缀（如 design_standard_doc, diagram_atlas_doc）
  if (typeLower.includes("document") || typeLower.includes("source") || typeLower.includes("_doc")) return "Source"

  return "entity"
}

// 快速操作按钮组件
function QuickActionComponent({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <Button
      variant="outline"
      onClick={onClick}
      className="flex items-center gap-2 rounded-full border transition-colors pointer-events-auto bg-transparent"
    >
      {icon}
      <span className="text-xs">{label}</span>
    </Button>
  )
}

// 初始状态组件
function InitialChatState({
  onQuickAction,
  message,
  setMessage,
  uploadedFiles,
  setUploadedFiles,
  handleSendMessage,
  deepSearch,
  setDeepSearch,
}: {
  onQuickAction: (text: string) => void
  message: string
  setMessage: (msg: string) => void
  uploadedFiles: File[]
  setUploadedFiles: (files: File[]) => void
  handleSendMessage: () => void
  deepSearch: boolean
  setDeepSearch: (deepSearch: boolean) => void
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 0.95, filter: "blur(10px)" }}
      transition={{ duration: 0.5 }}
      className="flex-1 flex flex-col items-center justify-center p-4 relative z-10"
    >
      <div className="w-full max-w-3xl flex flex-col items-center gap-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2, duration: 0.5 }}
          className="text-center space-y-4"
        >
          <h1 className="text-5xl md:text-7xl font-bold bg-gradient-to-br from-white via-gray-200 to-gray-400 bg-clip-text text-transparent drop-shadow-2xl tracking-tight">
            MediArch AI
          </h1>
          <p className="text-neutral-400 text-lg font-light tracking-wide">综合医院建筑设计问答助手</p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.5 }}
          className="w-full"
        >
          <ChatInput
            message={message}
            setMessage={setMessage}
            uploadedFiles={uploadedFiles}
            setUploadedFiles={setUploadedFiles}
            onSend={handleSendMessage}
            variant="initial"
            placeholder="输入您的问题..."
            deepSearch={deepSearch}
            setDeepSearch={setDeepSearch}
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5, duration: 0.5 }}
          className="flex flex-wrap justify-center gap-3"
        >
          <QuickActionComponent
            icon={<Stethoscope className="w-4 h-4" />}
            label="医疗流程设计"
            onClick={() => onQuickAction("请帮我设计一个高效的医疗流程")}
          />
          <QuickActionComponent
            icon={<Building2 className="w-4 h-4" />}
            label="建筑规范查询"
            onClick={() => onQuickAction("医疗建筑设计有哪些规范要求？")}
          />
          <QuickActionComponent
            icon={<FileSearch className="w-4 h-4" />}
            label="案例分析"
            onClick={() => onQuickAction("分析一个优秀的医疗建筑案例")}
          />
          <QuickActionComponent
            icon={<Lightbulb className="w-4 h-4" />}
            label="创新方案"
            onClick={() => onQuickAction("给我一些创新的医疗空间设计方案")}
          />
        </motion.div>
      </div>
    </motion.div>
  )
}

// 主 ChatInterface 组件逻辑
function ChatInterfaceContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const initialQuestion = searchParams.get("q")

  // 从 Context 获取状态
  const {
    messages,
    setMessages,
    isLoading,
    setIsLoading,
    streamingMessage,
    setStreamingMessage,
    isThinking,
    setIsThinking,
    activeAgentIndex,
    setActiveAgentIndex,
    currentThought,
    setCurrentThought,
    agentStatus,
    setAgentStatus,
    activeAgents,
    setActiveAgents,
    completedAgents,
    setCompletedAgents,
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
    graphData,
    setGraphData,
    isGraphAnimating,
    setIsGraphAnimating,
    showSuggestedQuestions,
    setShowSuggestedQuestions,
    isInitialState,
    setIsInitialState,
  } = useChatContext()

  // 本地状态
  const [message, setMessage] = useState("")
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([])
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(true)
  const [displayedSummary, setDisplayedSummary] = useState(DEFAULT_CONVERSATION_SUMMARY)
  const [backendSessionId, setBackendSessionId] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [recommendedQuestions, setRecommendedQuestions] = useState<string[]>([])
  const [deepSearch, setDeepSearch] = useState(false) // 深度检索模式

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const hasProcessedInitialQuestion = useRef(false)

  // 使用 useMemo 避免每次渲染创建新数组引用
  const agents = useMemo(() => [
    "Orchestrator Agent",
    "Neo4j Agent",
    "Milvus Agent",
    "MongoDB Agent",
    "Online Search Agent",
    "Result Synthesizer Agent",
  ], [])

  const agentThoughts = useMemo(() => ({
    "Orchestrator Agent": ["分析问题结构...", "制定查询策略...", "分配任务给各智能体..."],
    "Neo4j Agent": ["查询知识图谱...", "分析关系网络...", "提取关键节点..."],
    "Milvus Agent": ["向量相似度搜索...", "语义匹配分析...", "排序相关内容..."],
    "MongoDB Agent": ["检索文档数据...", "过滤相关记录...", "聚合结果集..."],
    "Online Search Agent": ["在线资源检索...", "验证最新信息...", "补充外部数据..."],
    "Result Synthesizer Agent": ["整合各方数据...", "生成综合答案...", "优化表达方式..."],
  }), [])

  // 默认建议问题（当后端没有返回时使用）
  const defaultSuggestedQuestions = ["能详细解释一下吗？", "有什么实际案例？", "如何实现这个功能？", "还有其他建议吗？"]

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, streamingMessage])

  // 对话摘要打字效果
  useEffect(() => {
    const target = conversationSummary || DEFAULT_CONVERSATION_SUMMARY
    if (!target) return

    let index = 0
    setDisplayedSummary("")
    const interval = setInterval(() => {
      index += 1
      setDisplayedSummary(target.slice(0, index))
      if (index >= target.length) {
        clearInterval(interval)
      }
    }, 45)

    return () => clearInterval(interval)
  }, [conversationSummary])

  // 自动标题更新
  useEffect(() => {
    if (!isAutoTitleActive) return
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        const newTitle = createAutoConversationTitle(messages[i].content)
        setConversationTitle(newTitle)
        break
      }
    }
    // 注意：setConversationTitle 来自 Context，是稳定的引用，不需要加入依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, isAutoTitleActive])

  // 处理初始问题
  useEffect(() => {
    if (initialQuestion && !hasProcessedInitialQuestion.current) {
      hasProcessedInitialQuestion.current = true
      setIsInitialState(false)
      setMessage(initialQuestion)
      requestAnimationFrame(() => {
        handleSendMessageWithText(initialQuestion)
      })
    }
  }, [initialQuestion])

  // 对话初始化
  useEffect(() => {
    if (currentConversationId !== undefined) return

    startTransition(() => {
      initializeDemoConversations()

      console.log("Creating new conversation for initial visit from homepage")
      const newId = `conv-${Date.now()}`
      const newConversation: StoredConversation = {
        id: newId,
        title: DEFAULT_CONVERSATION_TITLE,
        summary: DEFAULT_CONVERSATION_SUMMARY,
        messages: [],
        timestamp: new Date(),
        isPinned: false,
      }
      saveConversationToStorage(newConversation)
      setCurrentConversationId(newId)
      setIsInitialState(true)
    })
  }, [])

  const getStatusText = (status: AgentStatusUpdate) => status.thought || ""

  // 智能体名称映射
  const agentNameMap: Record<string, number> = {
    Orchestrator: 0,
    orchestrator: 0,
    orchestrator_agent: 0,
    "Orchestrator Agent": 0,
    Neo4j: 1,
    neo4j: 1,
    neo4j_agent: 1,
    "Neo4j Agent": 1,
    Milvus: 2,
    milvus: 2,
    milvus_agent: 2,
    "Milvus Agent": 2,
    MongoDB: 3,
    mongodb: 3,
    mongodb_agent: 3,
    "MongoDB Agent": 3,
    OnlineSearch: 4,
    online_search: 4,
    online_search_agent: 4,
    "Online Search Agent": 4,
    Synthesizer: 5,
    synthesizer: 5,
    result_synthesizer: 5,
    result_synthesizer_agent: 5,
    "Result Synthesizer Agent": 5,
  }

  // 处理智能体状态更新
  const handleAgentStatusUpdate = useCallback(
    (status: AgentStatusUpdate) => {
      const agentIndex = agentNameMap[status.agent_name] ?? -1
      if (agentIndex >= 0) {
        const statusText = getStatusText(status)
        if (status.status === "running" || status.status === "pending") {
          // 添加到活跃Agent集合
          setActiveAgents(prev => new Set(prev).add(agentIndex))
          setActiveAgentIndex(agentIndex)
          setIsThinking(true)
          setAgentStatus(agentIndex === 5 ? "synthesizing" : "thinking")
          // 运行状态：始终更新思考内容
          if (statusText) {
            setCurrentThought(statusText)
          }
        } else if (status.status === "completed" || status.status === "error") {
          // 从活跃Agent移除，添加到已完成集合
          setActiveAgents(prev => {
            const newSet = new Set(prev)
            newSet.delete(agentIndex)
            const [nextActive] = newSet
            setActiveAgentIndex(nextActive ?? agentIndex)
            // 只有在没有其他Agent运行时，才更新为完成消息
            // 避免已完成的Agent覆盖正在运行的Agent的思考内容
            if (newSet.size === 0 && statusText) {
              setCurrentThought(statusText)
            }
            return newSet
          })
          setCompletedAgents(prev => new Set(prev).add(agentIndex))

          // 只有综合器完成时才改为 idle
          if (agentIndex === 5 || status.status === "error") {
            setIsThinking(false)
            setAgentStatus("idle")
          }
        }
      }
    },
    // 这些 setter 函数来自 Context，是稳定的引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  )

  // 使用后端 API 发送消息（流式）
  const sendMessageWithBackendStream = async (text: string): Promise<{ content: string; citations: Citation[]; images: string[]; success: boolean }> => {
    setStreamingMessage("")
    setApiError(null)
    setIsThinking(true)
    setAgentStatus("thinking")
    setActiveAgentIndex(0)
    setGraphData({ nodes: [], links: [] })
    // 重置并行Agent状态
    setActiveAgents(new Set())
    setCompletedAgents(new Set())

    let accumulatedContent = ""
    let receivedCitations: Citation[] = []
    let receivedImages: string[] = []
    let hasError = false

    const callbacks: StreamCallbacks = {
      onSession: (sessionId) => {
        setBackendSessionId(sessionId)
        console.log("[API] Session ID:", sessionId)
      },
      onContent: (content) => {
        accumulatedContent += content
        setStreamingMessage(accumulatedContent)
      },
      onCitations: (citations) => {
        if (citations) {
          receivedCitations = citations
        }
      },
      onKnowledgeGraph: (data) => {
        if (data) {
          const converted = convertKnowledgeGraphData(data as KnowledgeGraphData)
          setGraphData(converted)
          setIsGraphAnimating(true)
          setTimeout(() => setIsGraphAnimating(false), 2000)
        }
      },
      onRecommendations: (questions) => {
        setRecommendedQuestions(questions)
      },
      onAgentStatus: (status) => {
        if (status) {
          handleAgentStatusUpdate(status as AgentStatusUpdate)
        }
      },
      onImages: (images) => {
        receivedImages = images
      },
      onDone: () => {
        setIsThinking(false)
        setAgentStatus("idle")
        setActiveAgentIndex(-1)
      },
      onError: (error) => {
        hasError = true
        // 只在真正的错误时记录，忽略正常流结束
        if (error && !error.includes("Stream ended")) {
          console.warn("[API] Stream error:", error)
          // 不设置 apiError，让降级逻辑处理
        }
        setIsThinking(false)
        setAgentStatus("idle")
      },
    }

    try {
      await chatApi.stream(
        {
          message: text,
          session_id: backendSessionId || undefined,
          include_online_search: false, // 测试阶段关闭
          include_citations: true,
          deep_search: deepSearch, // 深度检索模式
        },
        callbacks
      )
    } catch (error) {
      console.warn("[API] Stream request failed:", error)
      hasError = true
      setIsThinking(false)
      setAgentStatus("idle")
    }

    return {
      content: accumulatedContent,
      citations: receivedCitations,
      images: receivedImages,
      success: !hasError && accumulatedContent.length > 0,
    }
  }

  // 模拟流式响应（使用 Mock API 客户端）
  const simulateStreamResponse = async (text: string) => {
    let accumulatedContent = ""
    let receivedCitations: Citation[] = []
    let receivedImages: string[] = []
    let receivedKnowledgeGraph: KnowledgeGraphData | undefined = undefined
    let hasError = false

    const callbacks: StreamCallbacks = {
      onSession: (sessionId) => {
        console.log("[Mock] Session ID:", sessionId)
      },
      onContent: (content) => {
        accumulatedContent += content
        setStreamingMessage(accumulatedContent)
      },
      onCitations: (citations) => {
        if (citations) {
          receivedCitations = citations
        }
      },
      onImages: (images) => {
        if (images) {
          receivedImages = images
        }
      },
      onKnowledgeGraph: (data) => {
        if (data) {
          receivedKnowledgeGraph = data
          setGraphData(convertKnowledgeGraphData(data as KnowledgeGraphData))
        }
      },
      onRecommendations: (questions) => {
        if (questions) {
          setRecommendedQuestions(questions)
        }
      },
      onAgentStatus: (status) => {
        if (status) {
          // 使用统一的 Agent 状态处理函数
          handleAgentStatusUpdate(status as AgentStatusUpdate)
        }
      },
      onDone: () => {
        setIsThinking(false)
        setAgentStatus("idle")
      },
      onError: (error) => {
        console.error("[Mock] Error:", error)
        hasError = true
        setIsThinking(false)
        setAgentStatus("idle")
      },
    }

    try {
      await chatApi.stream(
        {
          message: text,
          include_citations: true,
          deep_search: deepSearch, // 深度检索模式
        },
        callbacks
      )
    } catch (error) {
      console.warn("[Mock] Stream request failed:", error)
      hasError = true
      setIsThinking(false)
      setAgentStatus("idle")
    }

    return {
      content: accumulatedContent,
      citations: receivedCitations,
      images: receivedImages,
      success: !hasError && accumulatedContent.length > 0,
    }
  }

  const updateConversationSummaryFromText = (text: string) => {
    const normalized = text.replace(/\s+/g, " ").trim()
    if (!normalized) return
    const summary = createConversationSummary(normalized)
    setConversationSummary(summary)
    if (isAutoTitleActive) {
      setConversationTitle(createAutoConversationTitle(normalized))
    }
  }

  const populateInput = (text: string) => {
    setMessage(text)
  }

  const handleSendMessageWithText = async (textOverride?: string) => {
    const textToSend = textOverride || message
    if ((!textToSend.trim() && uploadedFiles.length === 0) || isLoading) return

    setIsInitialState(false)
    setShowSuggestedQuestions(false)
    setApiError(null)
    setRecommendedQuestions([])

    const userMessage = {
      id: Date.now().toString(),
      role: "user" as const,
      content: textToSend,
      timestamp: new Date(),
      files: uploadedFiles.length > 0 ? uploadedFiles : undefined,
    }

    setMessages((prev) => [...prev, userMessage])
    setMessage("")
    setUploadedFiles([])
    setIsLoading(true)
    setStreamingMessage("")

    updateConversationSummaryFromText(textToSend)

    // 调用后端 API 或降级到模拟
    let result: { content: string; citations: Citation[]; images: string[] }

    if (USE_BACKEND_API) {
      const backendResult = await sendMessageWithBackendStream(textToSend)
      if (backendResult.success) {
        result = backendResult
      } else {
        console.warn("[API] Backend unavailable or returned empty, falling back to simulation")
        result = await simulateStreamResponse(textToSend)
      }
    } else {
      result = await simulateStreamResponse(textToSend)
    }

    // 转换引用为 PDFSource 格式
    const sources = citationsToPDFSources(result.citations)

    const assistantMessage = {
      id: (Date.now() + 1).toString(),
      role: "assistant" as const,
      content: result.content,
      timestamp: new Date(),
      sources: sources.length > 0 ? sources : undefined,
      images: result.images.length > 0 ? result.images : undefined,
    }

    setMessages((prev) => {
      const newMessages = [...prev, assistantMessage]

      // 保存对话到 localStorage
      if (currentConversationId) {
        const conversation = loadConversationFromStorage(currentConversationId)
        if (conversation) {
          conversation.messages = newMessages
          conversation.timestamp = new Date()
          saveConversationToStorage(conversation)
        }
      }

      return newMessages
    })

    setStreamingMessage("")
    setIsLoading(false)
    setShowSuggestedQuestions(true)
  }

  const handleRenameConversation = (nextTitle: string) => {
    setConversationTitle(nextTitle)
    setIsAutoTitleActive(false)

    if (currentConversationId) {
      const conversation = loadConversationFromStorage(currentConversationId)
      if (conversation) {
        conversation.title = nextTitle
        saveConversationToStorage(conversation)
      }
    }
  }

  const handlePinToggle = () => {
    setIsConversationPinned((prev) => {
      const newPinned = !prev

      if (currentConversationId) {
        const conversation = loadConversationFromStorage(currentConversationId)
        if (conversation) {
          conversation.isPinned = newPinned
          saveConversationToStorage(conversation)
        }
      }

      return newPinned
    })
  }

  const resetConversationMeta = () => {
    setConversationTitle(DEFAULT_CONVERSATION_TITLE)
    setIsConversationPinned(false)
    setIsAutoTitleActive(true)
    setConversationSummary(DEFAULT_CONVERSATION_SUMMARY)
    setDisplayedSummary(DEFAULT_CONVERSATION_SUMMARY)
    setBackendSessionId(null)
    setRecommendedQuestions([])
    setApiError(null)
  }

  const handleNewConversation = () => {
    const newConversationId = `conv-${Date.now()}`

    const newConversation: StoredConversation = {
      id: newConversationId,
      title: DEFAULT_CONVERSATION_TITLE,
      summary: DEFAULT_CONVERSATION_SUMMARY,
      messages: [],
      timestamp: new Date(),
      isPinned: false,
    }
    saveConversationToStorage(newConversation)
    setCurrentConversationId(newConversationId)

    setMessages([])
    setMessage("")
    setUploadedFiles([])
    setStreamingMessage("")
    setIsLoading(false)
    setShowSuggestedQuestions(false)
    setAgentStatus("idle")
    setActiveAgentIndex(-1)
    setGraphData({ nodes: [], links: [] })
    setIsGraphAnimating(false)
    setIsInitialState(true)
    resetConversationMeta()
    router.push("/chat")
  }

  const handleDeleteConversation = () => {
    const shouldDelete =
      typeof window === "undefined" ? true : window.confirm("确定删除当前对话吗？\n删除后内容将无法恢复。")
    if (!shouldDelete) return

    if (currentConversationId) {
      deleteConversationFromStorage(currentConversationId)
    }

    handleNewConversation()
  }

  const handleSuggestedQuestionClick = (question: string) => {
    populateInput(question)
  }

  const handleConversationSelect = (id: string) => {
    console.log("Selected conversation:", id)

    const conversation = loadConversationFromStorage(id)
    if (!conversation) {
      console.error("Conversation not found:", id)
      return
    }

    setCurrentConversationId(id)
    setMessages(conversation.messages)
    setConversationTitle(conversation.title)
    setConversationSummary(conversation.summary || DEFAULT_CONVERSATION_SUMMARY)
    setDisplayedSummary(conversation.summary || DEFAULT_CONVERSATION_SUMMARY)
    setIsConversationPinned(conversation.isPinned)
    setIsInitialState(conversation.messages.length === 0)
    setMessage("")
    setUploadedFiles([])
    setStreamingMessage("")
    setIsLoading(false)
    setShowSuggestedQuestions(false)
    setAgentStatus("idle")
    setActiveAgentIndex(-1)
    setGraphData({ nodes: [], links: [] })
    setIsGraphAnimating(false)
    setBackendSessionId(null)
    setRecommendedQuestions([])
    setApiError(null)

    console.log("Loaded conversation:", conversation.title, "with", conversation.messages.length, "messages")
  }

  // 使用后端返回的推荐问题或默认问题
  const displayedQuestions = recommendedQuestions.length > 0 ? recommendedQuestions : defaultSuggestedQuestions

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className="relative w-full h-screen flex flex-col overflow-hidden"
    >
      <div className="absolute inset-0 z-0">
        <EtherealShadow
          color="rgba(255, 255, 255, 0.35)"
          animation={{ scale: 100, speed: 30 }}
          noise={{ opacity: 0.3, scale: 1.5 }}
          sizing="fill"
        />
        <div className="absolute inset-0 bg-black" style={{ zIndex: -1 }} />
      </div>

      {/* API 错误提示 */}
      {apiError && (
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="fixed top-4 left-1/2 transform -translate-x-1/2 z-50 bg-red-500/90 text-white px-4 py-2 rounded-lg flex items-center gap-2 shadow-lg"
        >
          <AlertCircle className="w-4 h-4" />
          <span className="text-sm">{apiError}</span>
          <button onClick={() => setApiError(null)} className="ml-2 text-white/80 hover:text-white">
            x
          </button>
        </motion.div>
      )}

      <div className="relative z-10 flex h-full">
        {/* Conversation History Sidebar */}
        <ConversationSidebar
          isCollapsed={isSidebarCollapsed}
          onCollapsedChange={setIsSidebarCollapsed}
          onNewConversation={handleNewConversation}
          currentConversationId={currentConversationId}
          onConversationSelect={handleConversationSelect}
        />

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col min-w-0 relative">
          <AnimatePresence mode="wait">
            {isInitialState ? (
              <div key="initial-state" className="flex-1 flex flex-col h-full">
                <InitialChatState
                  onQuickAction={populateInput}
                  message={message}
                  setMessage={setMessage}
                  uploadedFiles={uploadedFiles}
                  setUploadedFiles={setUploadedFiles}
                  handleSendMessage={() => handleSendMessageWithText()}
                  deepSearch={deepSearch}
                  setDeepSearch={setDeepSearch}
                />
              </div>
            ) : (
              <div key="chat-interface" className="flex-1 flex flex-col h-full overflow-hidden relative">
                {messages.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, x: 20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.3, ease: "easeOut" }}
                    className="fixed top-4 right-4 z-40 lg:hidden"
                  >
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
                      className="text-white hover:bg-white/10 backdrop-blur-sm transition-all duration-300"
                      aria-label={isSidebarCollapsed ? "显示侧边栏" : "隐藏侧边栏"}
                    >
                      {isSidebarCollapsed ? (
                        <PanelRightOpen className="h-5 w-5" />
                      ) : (
                        <PanelRightClose className="h-5 w-5" />
                      )}
                    </Button>
                  </motion.div>
                )}

                <div className="sticky top-0 z-20">
                  <ConversationTopBar
                    title={conversationTitle}
                    summary={displayedSummary}
                    isPinned={isConversationPinned}
                    onPinToggle={handlePinToggle}
                    onRename={handleRenameConversation}
                    onDelete={handleDeleteConversation}
                  />
                </div>

                <div className="flex-1 flex flex-col min-h-0 px-2 lg:px-6">
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.5, ease: "easeInOut" }}
                    className="flex-1 grid min-h-0 gap-6 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_22rem] py-6"
                  >
                    <motion.div
                      initial={{ opacity: 0, x: -40 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.5, ease: "easeOut" }}
                      className="flex h-full flex-col min-h-0 overflow-hidden max-w-[70%] min-w-[800px] w-full mx-auto"
                    >
                      <ChatMessages agents={agents} />

                      {showSuggestedQuestions && !isLoading && messages.length > 0 && (
                        <motion.div
                          initial={{ opacity: 0, y: 10 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.3 }}
                          className="flex justify-start"
                        >
                          <div className="max-w-[75%]">
                            <SuggestedQuestions
                              questions={displayedQuestions}
                              onQuestionClick={handleSuggestedQuestionClick}
                            />
                          </div>
                        </motion.div>
                      )}

                      <div ref={messagesEndRef} />

                      <div className="mt-auto">
                        <ChatInput
                          message={message}
                          setMessage={setMessage}
                          uploadedFiles={uploadedFiles}
                          setUploadedFiles={setUploadedFiles}
                          onSend={() => handleSendMessageWithText()}
                          disabled={isLoading}
                          placeholder="继续对话..."
                          variant="conversation"
                          deepSearch={deepSearch}
                          setDeepSearch={setDeepSearch}
                        />
                      </div>
                    </motion.div>

                    <div className="hidden lg:flex flex-col min-h-0 gap-4">
                      <div className="min-h-[220px]">
                        <AgentThinkingPanel
                          activeAgentIndex={activeAgentIndex}
                          agents={agents}
                          agentStatus={agentStatus}
                          currentThought={currentThought}
                          isThinking={isThinking}
                          activeAgents={activeAgents}
                          completedAgents={completedAgents}
                        />
                      </div>
                      <div className="flex-1 min-h-[280px]">
                        <KnowledgeGraphPanel graphData={graphData} isAnimating={isGraphAnimating} />
                      </div>
                    </div>
                  </motion.div>
                </div>
              </div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  )
}

// 导出包装在 Provider 中的组件
export default function ChatInterface() {
  return (
    <ChatProvider>
      <ChatInterfaceContent />
    </ChatProvider>
  )
}
