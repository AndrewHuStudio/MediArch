/**
 * API 配置文件
 *
 * 管理 API 基础 URL 和其他配置项
 */

// API 基础配置
export const API_CONFIG = {
  // 后端 API 基础 URL
  BASE_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',

  // API 版本前缀
  API_PREFIX: '/api/v1',

  // 请求超时时间（毫秒）
  TIMEOUT: 60000,

  // SSE 心跳间隔（毫秒）
  SSE_HEARTBEAT_INTERVAL: 15000,
} as const

// 获取完整的 API URL
export function getApiUrl(path: string): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`
  return `${API_CONFIG.BASE_URL}${API_CONFIG.API_PREFIX}${cleanPath}`
}

// API 端点定义
export const API_ENDPOINTS = {
  // 对话相关
  CHAT: '/chat',
  CHAT_STREAM: '/chat/stream',
  SESSIONS: '/chat/sessions',
  SESSION_HISTORY: (sessionId: string) => `/chat/sessions/${sessionId}/history`,
  SESSION_DELETE: (sessionId: string) => `/chat/sessions/${sessionId}`,
  SESSION_UPDATE: (sessionId: string) => `/chat/sessions/${sessionId}`,

  // 知识库相关
  KB_CATEGORIES: '/kb/categories',
  KB_ITEMS: (categoryId: string) => `/kb/categories/${categoryId}/items`,
  KB_SEARCH: '/kb/search',

  // 健康检查
  HEALTH: '/health',
  HEALTH_DETAILED: '/health/detailed',
  METRICS: '/metrics',
} as const
