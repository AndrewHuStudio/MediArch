/**
 * API 类型定义
 *
 * 与后端 FastAPI schemas 对应的前端类型定义
 */

// ============================================================================
// 通用类型
// ============================================================================

export interface Citation {
  source: string
  location: string
  snippet: string
  chunk_id?: string
  page_number?: number
  section?: string
  metadata?: Record<string, unknown>
  page_range?: number[]
  chapter?: string
  chapter_title?: string
  sub_section?: string
  content_type?: string
  image_url?: string
  file_path?: string
  document_path?: string
  pdf_url?: string
  highlight_text?: string
  doc_id?: string
  doc_category?: string
  // PDF 高亮相关（与后端 positions 字段兼容）
  positions?: Array<Record<string, unknown>>
}

export interface DiagnosticInfo {
  took_ms?: number
  agent_name?: string
  query_type?: string
  items_count?: number
  error?: string
  additional_info?: Record<string, unknown>
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

// ============================================================================
// 对话相关类型
// ============================================================================

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: number
  citations?: Citation[]
}

export interface ChatRequest {
  message: string
  session_id?: string
  history?: ChatMessage[]
  top_k?: number
  include_online_search?: boolean
  stream?: boolean
  include_citations?: boolean
  include_diagnostics?: boolean
  deep_search?: boolean  // 深度检索模式
  thinking_mode?: boolean  // 思考模式（预留）
}

export interface ChatResponse {
  message: string
  session_id: string
  knowledge_graph_path?: KnowledgeGraphData
  citations?: Citation[]
  recommended_questions?: string[]
  diagnostics?: DiagnosticInfo[]
  took_ms?: number
  agents_used?: string[]
  images?: string[]
}

// SSE 流式响应块类型
export type StreamChunkType =
  | 'session'
  | 'content'
  | 'citations'
  | 'knowledge_graph'
  | 'recommendations'
  | 'agent_status'
  | 'diagnostics'
  | 'images'
  | 'done'
  | 'error'

export interface StreamingChatChunk {
  chunk_type: StreamChunkType
  content?: string
  citations?: Citation[]
  knowledge_graph_path?: KnowledgeGraphData
  recommended_questions?: string[]
  diagnostics?: DiagnosticInfo[]
  agent_status?: AgentStatusUpdate
  images?: string[]
  is_final: boolean
}

// 智能体状态更新
export interface AgentStatusUpdate {
  agent_name: string
  status: 'pending' | 'running' | 'completed' | 'error'
  thought?: string
  progress?: number
  took_ms?: number
}

// ============================================================================
// 知识图谱类型
// ============================================================================

export interface KnowledgeGraphNode {
  id: string
  label: string
  type: 'concept' | 'entity' | 'attribute' | 'relation' | 'document'
  properties?: Record<string, unknown>
}

export interface KnowledgeGraphLink {
  source: string
  target: string
  label: string
  weight?: number
}

export interface KnowledgeGraphData {
  nodes: KnowledgeGraphNode[]
  links: KnowledgeGraphLink[]
  // Neo4j 查询路径信息
  query_path?: {
    expanded_entities?: Array<{
      name: string
      type: string
      score: number
    }>
    expanded_relations?: Array<{
      source: string
      target: string
      relation: string
    }>
    knowledge_coverage?: number
  }
}

// ============================================================================
// 会话管理类型
// ============================================================================

export interface SessionInfo {
  session_id: string
  created_at: number
  last_active: number
  message_count: number
  title?: string
  is_pinned?: boolean
}

export interface SessionListResponse {
  sessions: SessionInfo[]
  total: number
}

export interface SessionHistoryResponse {
  session_id: string
  messages: ChatMessage[]
  total: number
}

export interface SessionUpdateRequest {
  title?: string
  is_pinned?: boolean
}

// ============================================================================
// 知识库类型
// ============================================================================

export interface KnowledgeBaseCategory {
  id: string
  name: string
  description?: string
  icon?: string
  item_count?: number
  tags?: string[]
}

export interface KnowledgeBaseItem {
  id: string
  title: string
  category: string
  source?: string
  description?: string
  tags?: string[]
  chunk_count?: number
  page_count?: number
  created_at?: number
  metadata?: Record<string, unknown>
}

export interface KnowledgeBaseSearchRequest {
  query: string
  category?: string
  tags?: string[]
  top_k?: number
}

export interface KnowledgeBaseSearchResponse {
  items: KnowledgeBaseItem[]
  total: number
  took_ms?: number
}

// ============================================================================
// 健康检查类型
// ============================================================================

export interface QuickHealthResponse {
  status: 'ok' | 'error'
  message: string
  timestamp: number
}

export interface ComponentStatus {
  name: string
  status: 'healthy' | 'unhealthy' | 'unknown'
  latency_ms?: number
  message?: string
  details?: Record<string, unknown>
  last_check?: number
}

export interface AgentStatus extends ComponentStatus {
  agent_type: 'worker' | 'supervisor'
  compilation_status: 'compiled' | 'failed'
  last_execution_ms?: number
}

export interface DatabaseStatus extends ComponentStatus {
  connection_pool_size?: number
  active_connections?: number
  version?: string
}

export interface SystemHealthResponse {
  overall_status: 'healthy' | 'degraded' | 'unhealthy'
  timestamp: number
  agents: AgentStatus[]
  databases: DatabaseStatus[]
  external_services: ComponentStatus[]
  system_metrics: Record<string, unknown>
}

// ============================================================================
// API 错误类型
// ============================================================================

export interface APIError {
  code: number
  message: string
  detail?: string
  path?: string
}

export class APIException extends Error {
  public code: number
  public detail?: string
  public path?: string

  constructor(error: APIError) {
    super(error.message)
    this.name = 'APIException'
    this.code = error.code
    this.detail = error.detail
    this.path = error.path
  }
}
