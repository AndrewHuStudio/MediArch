/**
 * useChat Hook
 *
 * 封装与后端对话 API 的交互逻辑
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { chatApi, StreamCallbacks } from '@/lib/api'
import type {
  ChatRequest,
  ChatResponse,
  Citation,
  KnowledgeGraphData,
  AgentStatusUpdate,
  SessionInfo,
} from '@/lib/api/types'

// 智能体配置
const AGENTS = [
  { name: 'Orchestrator', displayName: 'Orchestrator Agent' },
  { name: 'Neo4j', displayName: 'Neo4j Agent' },
  { name: 'Milvus', displayName: 'Milvus Agent' },
  { name: 'MongoDB', displayName: 'MongoDB Agent' },
  { name: 'OnlineSearch', displayName: 'Online Search Agent' },
  { name: 'Synthesizer', displayName: 'Result Synthesizer Agent' },
] as const

export interface AgentThinkingState {
  agentName: string
  displayName: string
  status: 'pending' | 'running' | 'completed' | 'error'
  thought: string
  progress: number
}

export interface UseChatOptions {
  sessionId?: string
  onSessionCreate?: (sessionId: string) => void
  includeOnlineSearch?: boolean
  includeCitations?: boolean
}

export interface UseChatReturn {
  // 状态
  isLoading: boolean
  isStreaming: boolean
  error: string | null
  sessionId: string | null
  streamingContent: string
  citations: Citation[]
  knowledgeGraph: KnowledgeGraphData | null
  recommendedQuestions: string[]
  agentStates: AgentThinkingState[]
  images: string[]

  // 方法
  sendMessage: (message: string, options?: Partial<ChatRequest>) => Promise<ChatResponse | null>
  sendMessageStream: (message: string, options?: Partial<ChatRequest>) => Promise<void>
  clearState: () => void
  setSessionId: (id: string | null) => void
}

export function useChat(options: UseChatOptions = {}): UseChatReturn {
  const {
    sessionId: initialSessionId,
    onSessionCreate,
    includeOnlineSearch = false,  // 默认关闭在线搜索（测试阶段）
    includeCitations = true,
  } = options

  // 状态
  const [isLoading, setIsLoading] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId || null)
  const [streamingContent, setStreamingContent] = useState('')
  const [citations, setCitations] = useState<Citation[]>([])
  const [knowledgeGraph, setKnowledgeGraph] = useState<KnowledgeGraphData | null>(null)
  const [recommendedQuestions, setRecommendedQuestions] = useState<string[]>([])
  const [images, setImages] = useState<string[]>([])
  const [agentStates, setAgentStates] = useState<AgentThinkingState[]>(
    AGENTS.map((agent) => ({
      agentName: agent.name,
      displayName: agent.displayName,
      status: 'pending' as const,
      thought: '',
      progress: 0,
    }))
  )

  // Refs
  const abortControllerRef = useRef<AbortController | null>(null)

  // 清理状态
  const clearState = useCallback(() => {
    setStreamingContent('')
    setCitations([])
    setKnowledgeGraph(null)
    setRecommendedQuestions([])
    setImages([])
    setError(null)
    setAgentStates(
      AGENTS.map((agent) => ({
        agentName: agent.name,
        displayName: agent.displayName,
        status: 'pending' as const,
        thought: '',
        progress: 0,
      }))
    )
  }, [])

  // 更新智能体状态
  const updateAgentState = useCallback((update: AgentStatusUpdate) => {
    setAgentStates((prev) =>
      prev.map((agent) => {
        // 匹配智能体名称（支持部分匹配）
        const isMatch =
          agent.agentName.toLowerCase().includes(update.agent_name.toLowerCase()) ||
          update.agent_name.toLowerCase().includes(agent.agentName.toLowerCase())

        if (isMatch) {
          return {
            ...agent,
            status: update.status,
            thought: update.thought || agent.thought,
            progress: update.progress ?? agent.progress,
          }
        }
        return agent
      })
    )
  }, [])

  // 发送消息（非流式）
  const sendMessage = useCallback(
    async (
      message: string,
      requestOptions: Partial<ChatRequest> = {}
    ): Promise<ChatResponse | null> => {
      clearState()
      setIsLoading(true)
      setError(null)

      try {
        const request: ChatRequest = {
          message,
          session_id: sessionId || undefined,
          include_online_search: includeOnlineSearch,
          include_citations: includeCitations,
          stream: false,
          ...requestOptions,
        }

        const response = await chatApi.send(request)

        // 更新状态
        if (response.session_id && response.session_id !== sessionId) {
          setSessionId(response.session_id)
          onSessionCreate?.(response.session_id)
        }

        if (response.citations) {
          setCitations(response.citations)
        }

        if (response.knowledge_graph_path) {
          setKnowledgeGraph(response.knowledge_graph_path)
        }

        if (response.recommended_questions) {
          setRecommendedQuestions(response.recommended_questions)
        }

        if (response.images) {
          setImages(response.images)
        }

        return response
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Unknown error'
        setError(errorMessage)
        return null
      } finally {
        setIsLoading(false)
      }
    },
    [sessionId, includeOnlineSearch, includeCitations, onSessionCreate, clearState]
  )

  // 发送消息（流式）
  const sendMessageStream = useCallback(
    async (
      message: string,
      requestOptions: Partial<ChatRequest> = {}
    ): Promise<void> => {
      clearState()
      setIsLoading(true)
      setIsStreaming(true)
      setError(null)

      // 模拟智能体开始工作
      setAgentStates((prev) =>
        prev.map((agent, index) => ({
          ...agent,
          status: index === 0 ? 'running' : 'pending',
        }))
      )

      const request: ChatRequest = {
        message,
        session_id: sessionId || undefined,
        include_online_search: includeOnlineSearch,
        include_citations: includeCitations,
        stream: true,
        ...requestOptions,
      }

      const callbacks: StreamCallbacks = {
        onSession: (newSessionId) => {
          if (newSessionId !== sessionId) {
            setSessionId(newSessionId)
            onSessionCreate?.(newSessionId)
          }
        },
        onContent: (content) => {
          setStreamingContent((prev) => prev + content)
        },
        onCitations: (newCitations) => {
          if (newCitations) {
            setCitations((prev) => [...prev, ...newCitations])
          }
        },
        onKnowledgeGraph: (data) => {
          if (data) {
            setKnowledgeGraph(data)
          }
        },
        onRecommendations: (questions) => {
          setRecommendedQuestions(questions)
        },
        onAgentStatus: (status) => {
          if (status) {
            updateAgentState(status)
          }
        },
        onImages: (newImages) => {
          setImages(newImages)
        },
        onDone: () => {
          setIsStreaming(false)
          setIsLoading(false)
          // 标记所有智能体完成
          setAgentStates((prev) =>
            prev.map((agent) => ({
              ...agent,
              status: 'completed',
            }))
          )
        },
        onError: (errorMsg) => {
          setError(errorMsg)
          setIsStreaming(false)
          setIsLoading(false)
        },
      }

      try {
        await chatApi.stream(request, callbacks)
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Unknown error'
        setError(errorMessage)
        setIsStreaming(false)
        setIsLoading(false)
      }
    },
    [
      sessionId,
      includeOnlineSearch,
      includeCitations,
      onSessionCreate,
      clearState,
      updateAgentState,
    ]
  )

  // 组件卸载时取消请求
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
    }
  }, [])

  return {
    // 状态
    isLoading,
    isStreaming,
    error,
    sessionId,
    streamingContent,
    citations,
    knowledgeGraph,
    recommendedQuestions,
    agentStates,
    images,

    // 方法
    sendMessage,
    sendMessageStream,
    clearState,
    setSessionId,
  }
}

// ============================================================================
// 会话管理 Hook
// ============================================================================

export interface UseSessionsReturn {
  sessions: SessionInfo[]
  isLoading: boolean
  error: string | null
  refresh: () => Promise<void>
  deleteSession: (sessionId: string) => Promise<boolean>
}

export function useSessions(): UseSessionsReturn {
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setIsLoading(true)
    setError(null)

    try {
      const response = await chatApi.getSessions()
      setSessions(response.sessions)
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load sessions'
      setError(errorMessage)
    } finally {
      setIsLoading(false)
    }
  }, [])

  const deleteSession = useCallback(async (sessionId: string): Promise<boolean> => {
    try {
      await chatApi.deleteSession(sessionId)
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId))
      return true
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete session'
      setError(errorMessage)
      return false
    }
  }, [])

  // 初始加载
  useEffect(() => {
    refresh()
  }, [refresh])

  return {
    sessions,
    isLoading,
    error,
    refresh,
    deleteSession,
  }
}
