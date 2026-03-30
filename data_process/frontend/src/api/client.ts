/** data_process API 客户端 */

const BASE = '/data-process'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`API ${res.status}: ${err}`)
  }
  return res.json()
}

// ---- 文件上传 ----
export async function uploadFile(file: File, category: string = '') {
  const form = new FormData()
  form.append('file', file)
  form.append('category', category)
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
  return res.json() as Promise<{ filename: string; size_bytes: number; saved_path: string; category: string }>
}

// ---- OCR ----
export interface OcrListItem {
  id: number
  filename: string
  category: string
  file_path: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  total_pages: number
  success_pages: number
  image_count: number
  ocr_dir: string | null
}

export function fetchOcrList(category?: string) {
  const query = category ? `?category=${encodeURIComponent(category)}` : ''
  return request<{ items: OcrListItem[]; total: number }>(`/ocr/list${query}`)
}

export async function uploadToDocuments(file: File, category: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('category', category)
  const res = await fetch(`${BASE}/ocr/upload-to-documents`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
  return res.json() as Promise<{ filename: string; size_bytes: number; saved_path: string; category: string }>
}

export function startOcr(filePath: string, category: string, pageStart?: number, pageEnd?: number) {
  return request<{ task_id: string; status: string }>('/ocr/process', {
    method: 'POST',
    body: JSON.stringify({ file_path: filePath, category, page_start: pageStart, page_end: pageEnd }),
  })
}

export function deleteOcrFile(filePath: string, category: string) {
  const query = `?file_path=${encodeURIComponent(filePath)}&category=${encodeURIComponent(category)}`
  return request<{ deleted: boolean; doc_path: string; ocr_dir_removed: string }>(`/ocr/file${query}`, {
    method: 'DELETE',
  })
}

// ---- 向量化 ----
export function startVectorize(ocrResult: Record<string, unknown>, docMetadata: Record<string, unknown>) {
  return request<{ task_id: string; status: string }>('/vector/process', {
    method: 'POST',
    body: JSON.stringify({ ocr_result: ocrResult, doc_metadata: docMetadata }),
  })
}

export interface VectorListItem {
  id: number
  filename: string
  category: string
  doc_path: string
  file_path: string
  ocr_dir: string | null
  status: 'pending' | 'running' | 'waiting_network' | 'completed' | 'failed'
  can_vectorize: boolean
  total_pages: number
  success_pages: number
  image_count: number
  vector_doc_id: string | null
  total_chunks: number
  version: number
  retry_count?: number
  next_retry_at?: string | null
  ocr_ready?: boolean
  ocr_reason?: string
  pdf_pages?: number
}

export function fetchVectorList(category?: string) {
  const query = category ? `?category=${encodeURIComponent(category)}` : ''
  return request<{ items: VectorListItem[]; total: number }>(`/vector/list${query}`)
}

export interface PipelineOverviewItem {
  doc_path: string
  filename: string
  category: string
  ocr_status: 'pending' | 'running' | 'completed' | 'failed'
  vector_status: 'pending' | 'running' | 'waiting_network' | 'completed' | 'failed'
  can_vectorize: boolean
  vector_doc_id: string | null
  can_graphize: boolean
}

export interface PipelineOverviewResponse {
  summary: {
    uploaded_total: number
    ocr_completed: number
    vector_completed: number
    kg: {
      task_id: string | null
      status: 'idle' | 'pending' | 'running' | 'completed' | 'failed'
      progress_percent: number
      stage: string
      updated_at: string | null
    }
  }
  items: PipelineOverviewItem[]
  total: number
}

export function fetchPipelineOverview() {
  return request<PipelineOverviewResponse>('/pipeline/overview')
}

export function startVectorizeFromOcr(filePath: string, category: string, title?: string, force: boolean = false) {
  return request<{ task_id: string; status: string }>('/vector/process-from-ocr', {
    method: 'POST',
    body: JSON.stringify({ file_path: filePath, category, title, force }),
  })
}

export function rerank(query: string, chunks: Record<string, unknown>[], topK: number = 10) {
  return request<{ query: string; results: Record<string, unknown>[]; total: number }>('/vector/rerank', {
    method: 'POST',
    body: JSON.stringify({ query, chunks, top_k: topK }),
  })
}

// ---- KG ----
export function startKgBuild(params: {
  source?: string; mongo_doc_ids?: string[]; chunks?: Record<string, unknown>[];
  ea_max_rounds?: number; ea_threshold?: number; rel_max_rounds?: number; rel_threshold?: number;
  strategy?: string; custom_config?: Record<string, unknown>; experiment_label?: string; save_to_history?: boolean;
}) {
  return request<{ task_id: string; status: string }>('/kg/build', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export function startKgStage(params: {
  stage: string; chunks?: Record<string, unknown>[];
  ea_pairs?: Record<string, unknown>[]; triplets?: Record<string, unknown>[];
  mongo_doc_ids?: string[];
}) {
  return request<{ task_id: string; status: string }>('/kg/stage', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

// ---- 任务状态 ----
export function getTaskStatus(taskId: string) {
  return request<{
    task_id: string; status: string;
    progress: Record<string, unknown> | null;
    result: Record<string, unknown> | null;
    error: string | null;
    error_hint?: string | null;
    created_at?: string | null;
  }>(`/tasks/${taskId}`)
}
