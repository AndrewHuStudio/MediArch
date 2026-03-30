/**
 * API 客户端
 *
 * 提供与后端 FastAPI 交互的方法
 * 支持 Mock 模式（无后端演示）
 */

import { API_CONFIG, getApiBaseUrlCandidates, getApiUrl, API_ENDPOINTS } from './config'
import {
  ChatRequest,
  ChatResponse,
  StreamingChatChunk,
  SessionListResponse,
  SessionHistoryResponse,
  SessionUpdateRequest,
  KnowledgeBaseCategory,
  KnowledgeBaseSearchRequest,
  KnowledgeBaseSearchResponse,
  QuickHealthResponse,
  SystemHealthResponse,
  APIException,
  PaginatedResponse,
  KnowledgeBaseItem,
} from './types'

// 导入 Mock 客户端
import {
  mockChatRequest,
  mockChatStreamRequest,
  mockHealthCheck,
  mockGetSessions,
  mockGetSessionHistory,
} from './mock-client'
import { requestTranslationWithCandidates } from './translate-client'

// 检查是否使用后端 API（可以通过环境变量控制）
const USE_BACKEND_API = process.env.NEXT_PUBLIC_USE_BACKEND_API !== 'false'

// Backend availability probe (auto fallback to mock)
const BACKEND_DETECT_TIMEOUT_MS = 1200
const BACKEND_DETECT_TTL_MS = 8000

let backendAvailabilityCache: { ok: boolean; checkedAt: number; baseUrl: string | null } | null = null

async function resolveBackendBaseUrl(): Promise<string | null> {
  if (!USE_BACKEND_API) return null

  const now = Date.now()
  if (backendAvailabilityCache && now - backendAvailabilityCache.checkedAt < BACKEND_DETECT_TTL_MS) {
    return backendAvailabilityCache.ok ? backendAvailabilityCache.baseUrl : null
  }

  for (const baseUrl of getApiBaseUrlCandidates()) {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), BACKEND_DETECT_TIMEOUT_MS)

    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.HEALTH, baseUrl), {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
        cache: 'no-store',
      })
      if (response.ok) {
        backendAvailabilityCache = { ok: true, checkedAt: now, baseUrl }
        return baseUrl
      }
    } catch {
      // continue probing the next candidate
    } finally {
      clearTimeout(timeoutId)
    }
  }

  backendAvailabilityCache = { ok: false, checkedAt: now, baseUrl: null }
  return null
}

// ============================================================================
// HTTP 请求工具
// ============================================================================

interface RequestOptions extends RequestInit {
  timeout?: number
}

function isRetryableChatTransportError(error: unknown): boolean {
  const message =
    error instanceof APIException
      ? String(error.message || "").toLowerCase()
      : String(error instanceof Error ? error.message : error || "").toLowerCase()

  if (!message) return false

  return [
    "failed to connect",
    "backend not available",
    "network error",
    "fetch failed",
    "econnrefused",
    "ecconnrefused",
    "connection refused",
    "connection reset",
    "socket hang up",
    "aborterror",
    "stream error:",
    "request timeout",
  ].some((marker) => message.includes(marker))
}

async function request<T>(
  url: string,
  options: RequestOptions = {}
): Promise<T> {
  const { timeout = API_CONFIG.TIMEOUT, ...fetchOptions } = options

  const controller = new AbortController()
  const timeoutId =
    typeof timeout === 'number' && timeout > 0
      ? setTimeout(() => controller.abort(), timeout)
      : null

  try {
    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...fetchOptions.headers,
      },
    })

    if (timeoutId) {
      clearTimeout(timeoutId)
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new APIException({
        code: response.status,
        message: errorData.detail || response.statusText,
        detail: errorData.error?.detail,
        path: url,
      })
    }

    return response.json()
  } catch (error) {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }
    if (error instanceof APIException) {
      throw error
    }
    if (error instanceof Error && error.name === 'AbortError') {
      throw new APIException({
        code: 408,
        message: 'Request timeout',
        path: url,
      })
    }
    throw new APIException({
      code: 500,
      message: error instanceof Error ? error.message : 'Unknown error',
      path: url,
    })
  }
}

// ============================================================================
// SSE 流式请求处理
// ============================================================================

export interface StreamCallbacks {
  onSession?: (sessionId: string) => void
  onContent?: (content: string) => void
  onCitations?: (citations: StreamingChatChunk['citations']) => void
  onKnowledgeGraph?: (data: StreamingChatChunk['knowledge_graph_path']) => void
  onRecommendations?: (questions: string[]) => void
  onAgentStatus?: (status: StreamingChatChunk['agent_status']) => void
  onImages?: (images: string[]) => void
  onDone?: () => void
  onError?: (error: string) => void
}

/**
 * 处理单个SSE事件数据
 */
function processSSEData(eventData: string, callbacks: StreamCallbacks): void {
  // SSE事件可能包含多行，但我们只关心data:行
  const lines = eventData.split('\n')
  let jsonStr = ''

  for (const line of lines) {
    if (line.startsWith('data: ')) {
      // 累积data内容（处理多行data的情况）
      jsonStr += line.slice(6)
    } else if (line.startsWith('data:')) {
      jsonStr += line.slice(5)
    }
  }

  jsonStr = jsonStr.trim()
  if (!jsonStr) return

  try {
    const chunk: StreamingChatChunk = JSON.parse(jsonStr)

    switch (chunk.chunk_type) {
      case 'session':
        callbacks.onSession?.(chunk.content || '')
        break
      case 'content':
        callbacks.onContent?.(chunk.content || '')
        break
      case 'citations':
        callbacks.onCitations?.(chunk.citations)
        break
      case 'knowledge_graph':
        callbacks.onKnowledgeGraph?.(chunk.knowledge_graph_path)
        break
      case 'recommendations':
        callbacks.onRecommendations?.(chunk.recommended_questions || [])
        break
      case 'agent_status':
        callbacks.onAgentStatus?.(chunk.agent_status)
        break
      case 'images':
        callbacks.onImages?.(chunk.images || [])
        break
      case 'done':
        callbacks.onDone?.()
        break
      case 'error':
        console.warn('[streamRequest] Server returned error chunk:', chunk.content)
        callbacks.onError?.(chunk.content || 'Unknown error')
        break
    }
  } catch (parseError) {
    // 只在调试模式下输出解析失败的日志，避免污染控制台
    if (process.env.NODE_ENV === 'development') {
      // 截断过长的内容，避免控制台被大量数据淹没
      const truncated = jsonStr.length > 500 ? jsonStr.slice(0, 500) + '...[truncated]' : jsonStr
      console.warn('[SSE] Failed to parse chunk (may be incomplete):', truncated)
    }
  }
}

async function streamRequest(
  url: string,
  body: unknown,
  callbacks: StreamCallbacks
): Promise<void> {
  let didCallDone = false
  const wrappedCallbacks: StreamCallbacks = {
    ...callbacks,
    onDone: () => {
      if (didCallDone) return
      didCallDone = true
      callbacks.onDone?.()
    },
  }

  let response: Response

  console.log('[streamRequest] Starting request to:', url)

  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(body),
    })
    console.log('[streamRequest] Response received, status:', response.status)
  } catch (error) {
    // 网络错误、CORS 错误等 - 静默处理，让降级逻辑工作
    const message = error instanceof Error ? error.message : 'Network error'
    console.warn('[streamRequest] Backend not available:', message)
    callbacks.onError?.(`Failed to connect: ${message}`)
    return
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    console.warn('[streamRequest] Response not OK:', response.status, errorData)
    callbacks.onError?.(errorData.detail || response.statusText)
    return
  }

  const reader = response.body?.getReader()
  if (!reader) {
    callbacks.onError?.('No response body')
    return
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        console.log('[streamRequest] Stream completed normally')
        if (buffer.trim()) {
          processSSEData(buffer, wrappedCallbacks)
        }
        wrappedCallbacks.onDone?.()
        break
      }

      buffer += decoder.decode(value, { stream: true })

      // SSE事件以双换行符分隔
      const events = buffer.split('\n\n')
      // 最后一个可能是不完整的事件，保留在buffer中
      buffer = events.pop() || ''

      for (const event of events) {
        if (!event.trim()) continue
        processSSEData(event, wrappedCallbacks)
      }
    }
  } catch (error) {
    // 读取流时出错 - 静默处理
    const message = error instanceof Error ? error.message : 'Stream read error'
    console.warn('[streamRequest] Stream read error:', message)
    callbacks.onError?.(`Stream error: ${message}`)
  } finally {
    reader.releaseLock()
  }
}

// ============================================================================
// Chat API
// ============================================================================

export const chatApi = {
  /**
   * 发送对话消息（非流式）
   */
  async send(req: ChatRequest): Promise<ChatResponse> {
    // Mock 模式
    if (!USE_BACKEND_API) {
      console.log('[chatApi] Using mock mode')
      return mockChatRequest(req)
    }

    let lastError: unknown = null

    for (const baseUrl of getApiBaseUrlCandidates()) {
      try {
        return await request<ChatResponse>(getApiUrl(API_ENDPOINTS.CHAT, baseUrl), {
          method: 'POST',
          body: JSON.stringify(req),
          timeout: 0,
        })
      } catch (error) {
        lastError = error
        if (!isRetryableChatTransportError(error)) {
          throw error
        }
      }
    }

    throw lastError instanceof Error
      ? lastError
      : new APIException({
          code: 500,
          message: 'Backend not available',
          path: getApiUrl(API_ENDPOINTS.CHAT),
        })
  },

  /**
   * 发送对话消息（流式）
   */
  async stream(req: ChatRequest, callbacks: StreamCallbacks): Promise<void> {
    // Mock 模式
    if (!USE_BACKEND_API) {
      console.log('[chatApi] Using mock stream mode')
      return mockChatStreamRequest(req, callbacks)
    }

    let lastError = 'Backend not available'

    for (const baseUrl of getApiBaseUrlCandidates()) {
      let attemptError: string | undefined
      const attemptCallbacks: StreamCallbacks = {
        ...callbacks,
        onError: (error) => {
          attemptError = error
        },
      }

      await streamRequest(getApiUrl(API_ENDPOINTS.CHAT_STREAM, baseUrl), req, attemptCallbacks)

      if (!attemptError) {
        return
      }

      lastError = attemptError
      if (!isRetryableChatTransportError(attemptError)) {
        callbacks.onError?.(attemptError)
        return
      }
    }

    callbacks.onError?.(lastError)
  },

  /**
   * 获取会话列表
   */
  async getSessions(): Promise<SessionListResponse> {
    // Mock 模式
    if (!USE_BACKEND_API) {
      return mockGetSessions()
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      return mockGetSessions()
    }

    return request<SessionListResponse>(getApiUrl(API_ENDPOINTS.SESSIONS, baseUrl))
  },

  /**
   * 获取会话历史
   */
  async getSessionHistory(sessionId: string): Promise<SessionHistoryResponse> {
    // Mock 模式
    if (!USE_BACKEND_API) {
      return mockGetSessionHistory(sessionId)
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      return mockGetSessionHistory(sessionId)
    }

    return request<SessionHistoryResponse>(
      getApiUrl(API_ENDPOINTS.SESSION_HISTORY(sessionId), baseUrl)
    )
  },

  /**
   * 删除会话
   */
  async deleteSession(sessionId: string): Promise<{ message: string; session_id: string }> {
    // Mock 模式下不支持
    if (!USE_BACKEND_API) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    return request(getApiUrl(API_ENDPOINTS.SESSION_DELETE(sessionId), baseUrl), {
      method: 'DELETE',
    })
  },

  /**
   * 更新会话（标题、置顶状态等）
   */
  async updateSession(
    sessionId: string,
    data: SessionUpdateRequest
  ): Promise<{ message: string; session_id: string }> {
    // Mock 模式下不支持
    if (!USE_BACKEND_API) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    return request(getApiUrl(API_ENDPOINTS.SESSION_UPDATE(sessionId), baseUrl), {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
  },
}

// ============================================================================
// Knowledge Base API
// ============================================================================

export const knowledgeBaseApi = {
  /**
   * 获取知识库分类
   */
  async getCategories(): Promise<{ categories: KnowledgeBaseCategory[] }> {
    return request(getApiUrl(API_ENDPOINTS.KB_CATEGORIES))
  },

  /**
   * 获取分类下的条目
   */
  async getCategoryItems(
    categoryId: string,
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<KnowledgeBaseItem>> {
    const url = `${getApiUrl(API_ENDPOINTS.KB_ITEMS(categoryId))}?page=${page}&page_size=${pageSize}`
    return request(url)
  },

  /**
   * 搜索知识库
   */
  async search(req: KnowledgeBaseSearchRequest): Promise<KnowledgeBaseSearchResponse> {
    return request(getApiUrl(API_ENDPOINTS.KB_SEARCH), {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
}

// ============================================================================
// Health API
// ============================================================================

export const healthApi = {
  /**
   * 快速健康检查
   */
  async check(): Promise<QuickHealthResponse> {
    // Mock 模式
    if (!USE_BACKEND_API) {
      return mockHealthCheck()
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      return mockHealthCheck()
    }

    return request(getApiUrl(API_ENDPOINTS.HEALTH, baseUrl))
  },

  /**
   * 详细健康检查
   */
  async detailed(): Promise<SystemHealthResponse> {
    // Mock 模式下不支持
    if (!USE_BACKEND_API) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    return request(getApiUrl(API_ENDPOINTS.HEALTH_DETAILED, baseUrl))
  },

  /**
   * 获取系统指标
   */
  async metrics(): Promise<Record<string, unknown>> {
    // Mock 模式下不支持
    if (!USE_BACKEND_API) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    const baseUrl = await resolveBackendBaseUrl()
    if (!baseUrl) {
      throw new APIException({
        code: 501,
        message: 'Not implemented in mock mode',
      })
    }

    return request(getApiUrl(API_ENDPOINTS.METRICS, baseUrl))
  },
}

// ============================================================================
// Translate API
// ============================================================================

export async function translateText(text: string, targetLang: 'en' | 'zh'): Promise<string> {
  return requestTranslationWithCandidates({
    text,
    targetLang,
    requestFn: async (url, body) =>
      request<{ translated: string }>(url, {
        method: 'POST',
        body: JSON.stringify(body),
        timeout: 0,
      }),
  })
}

// ============================================================================
// 统一导出
// ============================================================================

export const api = {
  chat: chatApi,
  kb: knowledgeBaseApi,
  health: healthApi,
}

export default api
