type TaskState = 'idle' | 'pending' | 'running' | 'completed' | 'failed'

type ProgressPayload = {
  stage?: string
  current?: number
  total?: number
  message?: string
  extra?: Record<string, unknown>
} | null

const STAGE_META = [
  { key: 'ea_recognition', label: 'E-A 识别', weight: 0.32, defaultStep: 'Chunk 处理进度' },
  { key: 'relation_extraction', label: '关系抽取', weight: 0.32, defaultStep: 'Chunk 处理进度' },
  { key: 'triplet_optimization', label: '三元组优化', weight: 0.16, defaultStep: '优化步骤推进' },
  { key: 'cross_document_fusion', label: '跨文档融合', weight: 0.2, defaultStep: '融合步骤推进' },
] as const

const STEP_LABELS: Record<string, string> = {
  ea_recognition: 'Chunk 处理进度',
  relation_extraction: 'Chunk 处理进度',
  optimization_start: '开始优化',
  name_standardization_done: '实体名称标准化',
  relation_normalization_done: '关系归一化',
  validation_done: '验证与去重',
  fusion_start: '开始融合',
  entity_dedup_done: '实体去重',
  latent_recognition_done: '潜在关系识别',
  neo4j_write_done: '写入 Neo4j',
}

function toMs(value: string | number | Date | null | undefined) {
  if (value == null) return null
  const ms = value instanceof Date ? value.getTime() : new Date(value).getTime()
  return Number.isFinite(ms) ? ms : null
}

function toNumber(value: unknown) {
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

function toStringValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

export function formatDurationMs(ms: number) {
  const safeMs = Number.isFinite(ms) ? Math.max(0, Math.round(ms)) : 0
  const totalSeconds = Math.floor(safeMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours > 0) {
    return `${hours}小时 ${minutes}分`
  }
  return `${minutes}分 ${seconds}秒`
}

export function getKgProgressSnapshot(input: {
  state: TaskState
  progress?: ProgressPayload
  createdAt?: string | null
  now?: string | number | Date
}) {
  const extra = (input.progress?.extra && typeof input.progress.extra === 'object'
    ? input.progress.extra
    : null) as Record<string, unknown> | null
  const backendExtra = extra?.progress_kind === 'kg_build' ? extra : null
  const nowMs = toMs(input.now ?? Date.now()) ?? Date.now()
  const createdAtMs = toMs(input.createdAt)
  const elapsedMsFallback = createdAtMs == null ? 0 : Math.max(0, nowMs - createdAtMs)
  const rawStage = String(input.progress?.stage ?? '')
  const [stageKeyRaw, stepKeyRaw] = rawStage.split(':')
  const stageKey = stageKeyRaw || ''
  const stepKey = stepKeyRaw || stageKey
  const current = Number(input.progress?.current ?? 0)
  const total = Number(input.progress?.total ?? 0)
  const stageFraction = input.state === 'completed'
    ? 1
    : total > 0
    ? Math.max(0, Math.min(1, current / total))
    : 0

  const stageIndex = STAGE_META.findIndex((stage) => stage.key === stageKey)
  const stageMeta = STAGE_META[stageIndex] ?? null
  const completedWeight = stageIndex <= 0
    ? 0
    : STAGE_META.slice(0, stageIndex).reduce((sum, stage) => sum + stage.weight, 0)

  const overallFraction = input.state === 'completed'
    ? 1
    : stageMeta
    ? Math.max(0, Math.min(0.99, completedWeight + stageMeta.weight * stageFraction))
    : input.state === 'running'
    ? 0
    : 0

  const overallPercentFallback = Math.round(overallFraction * 100)
  const stagePercentFallback = input.state === 'completed' ? 100 : Math.round(stageFraction * 100)
  const estimatedTotalMsFallback = overallFraction > 0 ? Math.round(elapsedMsFallback / overallFraction) : null
  const remainingMsFallback = estimatedTotalMsFallback == null ? null : Math.max(0, estimatedTotalMsFallback - elapsedMsFallback)

  const elapsedMs = backendExtra && toNumber(backendExtra.elapsed_seconds) != null
    ? Math.round((toNumber(backendExtra.elapsed_seconds) ?? 0) * 1000)
    : elapsedMsFallback
  const estimatedTotalMs = backendExtra && toNumber(backendExtra.estimated_total_seconds) != null
    ? Math.round((toNumber(backendExtra.estimated_total_seconds) ?? 0) * 1000)
    : estimatedTotalMsFallback
  const remainingMs = backendExtra && toNumber(backendExtra.remaining_seconds) != null
    ? Math.round((toNumber(backendExtra.remaining_seconds) ?? 0) * 1000)
    : remainingMsFallback
  const overallPercent = input.state === 'completed'
    ? 100
    : backendExtra && toNumber(backendExtra.overall_percent) != null
    ? Math.round(toNumber(backendExtra.overall_percent) ?? 0)
    : overallPercentFallback
  const stagePercent = input.state === 'completed'
    ? 100
    : backendExtra && toNumber(backendExtra.stage_percent) != null
    ? Math.round(toNumber(backendExtra.stage_percent) ?? 0)
    : stagePercentFallback
  const displayCurrent = backendExtra && toNumber(backendExtra.current_display) != null
    ? Math.round(toNumber(backendExtra.current_display) ?? 0)
    : current
  const displayTotal = backendExtra && toNumber(backendExtra.total_display) != null
    ? Math.round(toNumber(backendExtra.total_display) ?? 0)
    : total
  const estimateSource = toStringValue(backendExtra?.estimate_source)
  const historySampleCount = Math.max(0, Math.round(toNumber(backendExtra?.history_sample_count) ?? 0))

  let etaLabel = '计算中'
  if (input.state === 'completed') etaLabel = '已完成'
  else if (input.state === 'pending') etaLabel = '等待开始'
  else if (remainingMs != null && (backendExtra != null || overallFraction > 0)) etaLabel = formatDurationMs(remainingMs)

  const estimateSourceLabel = estimateSource === 'stage_blended'
    ? `按阶段实时进度 + ${historySampleCount} 次历史校准`
    : estimateSource === 'stage_history'
    ? `基于 ${historySampleCount} 次分阶段历史校准`
    : estimateSource === 'blended'
    ? `实时进度 + ${historySampleCount} 次历史校准`
    : estimateSource === 'history'
    ? `基于 ${historySampleCount} 次历史校准`
    : estimateSource === 'runtime'
    ? '基于实时进度估算'
    : '本地兜底估算'

  return {
    overallPercent: input.state === 'failed' && overallPercent === 0 ? 0 : overallPercent,
    stagePercent,
    stageKey: toStringValue(backendExtra?.stage_key) || stageKey,
    stageLabel: toStringValue(backendExtra?.stage_label) || (stageMeta?.label
      ?? (input.state === 'completed'
        ? '构建完成'
        : input.state === 'failed'
        ? '构建失败'
        : input.state === 'running' || input.state === 'pending'
        ? '准备构建'
        : '未开始')),
    stepLabel: toStringValue(backendExtra?.step_label) || (STEP_LABELS[stepKey]
      ?? (input.state === 'completed'
        ? '最终结果已生成'
        : stageMeta?.defaultStep ?? '处理中')),
    elapsedMs,
    elapsedLabel: formatDurationMs(elapsedMs),
    etaMs: remainingMs,
    etaLabel,
    estimatedTotalMs,
    estimatedTotalLabel: estimatedTotalMs == null ? '计算中' : formatDurationMs(estimatedTotalMs),
    detailMessage: String(input.progress?.message ?? '').trim() || toStringValue(backendExtra?.step_label),
    current: displayCurrent,
    total: displayTotal,
    estimateSource,
    estimateSourceLabel,
    historySampleCount,
    totalChunks: Math.round(toNumber(backendExtra?.total_chunks) ?? 0),
  }
}
