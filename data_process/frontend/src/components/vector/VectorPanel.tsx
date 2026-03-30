import { useState, useCallback, useRef, useEffect } from 'react'
import { Play, FileText, RefreshCw, CheckCircle2, RotateCcw } from 'lucide-react'
import { fetchVectorList, startVectorizeFromOcr, getTaskStatus } from '@/api/client'
import type { VectorListItem } from '@/api/client'
import { ProgressBar, StatusBadge } from '@/components/shared/ProgressBar'
import {
  VECTOR_ALL_CATEGORY,
  VECTOR_CATEGORIES,
  getVectorQueryCategory,
  isSelectableForVector,
  isForceRerunnableForVector,
} from './vectorPanelState'

const VECTOR_SELECTED_STORAGE_KEY = 'vector:selectedPaths'
const VECTOR_CATEGORY_STORAGE_KEY = 'vector:activeCategory'

interface LocalItem extends VectorListItem {
  progress: number
  taskId: string | null
  error: string | null
}

function getWaitingNetworkMessage(error: string | null, retryCount?: number): string {
  const msg = (error ?? '').trim()
  const suffix = `（重试 ${retryCount ?? 0}）`
  if (!msg) return `等待依赖服务恢复 ${suffix}`
  return `${msg} ${suffix}`
}

export function VectorPanel() {
  const [activeCategory, setActiveCategory] = useState<string>(() => {
    if (typeof window === 'undefined') return VECTOR_ALL_CATEGORY
    const saved = window.sessionStorage.getItem(VECTOR_CATEGORY_STORAGE_KEY)
    return saved && VECTOR_CATEGORIES.includes(saved as (typeof VECTOR_CATEGORIES)[number])
      ? saved
      : VECTOR_ALL_CATEGORY
  })
  const [items, setItems] = useState<LocalItem[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() => {
    if (typeof window === 'undefined') return new Set()
    try {
      const raw = window.sessionStorage.getItem(VECTOR_SELECTED_STORAGE_KEY)
      const arr = raw ? JSON.parse(raw) : []
      if (!Array.isArray(arr)) return new Set()
      return new Set(arr.filter((v): v is string => typeof v === 'string' && v.length > 0))
    } catch {
      return new Set()
    }
  })
  const pollersRef = useRef<Record<string, number>>({})

  const updateItem = useCallback((id: number, patch: Partial<LocalItem>) => {
    setItems(prev => prev.map(it => (it.id === id ? { ...it, ...patch } : it)))
  }, [])

  const clearPollers = useCallback(() => {
    Object.values(pollersRef.current).forEach(t => window.clearTimeout(t))
    pollersRef.current = {}
  }, [])

  const loadList = useCallback(async () => {
    setRefreshing(true)
    try {
      const res = await fetchVectorList(getVectorQueryCategory(activeCategory))
      setItems(prev => {
        const runningMap = new Map<string, LocalItem>()
        for (const it of prev) {
          if ((it.status === 'running' || it.status === 'waiting_network') && it.taskId) {
            runningMap.set(it.file_path, it)
          }
        }
        return res.items.map(item => {
          const running = runningMap.get(item.file_path)
          const backendIsRunning = item.status === 'running' || item.status === 'waiting_network'
          if (running && backendIsRunning) {
            return {
              ...item,
              status: running.status,
              progress: running.progress,
              taskId: running.taskId,
              error: running.error,
            }
          }
          return {
            ...item,
            progress: item.status === 'completed' ? 100 : 0,
            taskId: null,
            error: null,
          }
        })
      })
    } catch (e) {
      console.error('Failed to load vector list:', e)
    } finally {
      setRefreshing(false)
    }
  }, [activeCategory])

  const pollTask = useCallback((itemId: number, taskId: string) => {
    const poll = async () => {
      try {
        const res = await getTaskStatus(taskId)
        if (res.status === 'completed') {
          delete pollersRef.current[taskId]
          await loadList()
          return
        }
        if (res.status === 'waiting_network') {
          const retryCount = Number((res.progress as Record<string, unknown> | null)?.retry_count ?? 0)
          updateItem(itemId, {
            status: 'waiting_network',
            error: res.error_hint ?? res.error ?? null,
            retry_count: retryCount,
          })
          pollersRef.current[taskId] = window.setTimeout(poll, 2500)
          return
        }
        if (res.status === 'failed') {
          delete pollersRef.current[taskId]
          updateItem(itemId, { status: 'failed', error: res.error ?? 'Unknown error', progress: 0 })
          return
        }
        if (res.status === 'running') {
          updateItem(itemId, { status: 'running', error: null })
        }
        if (res.progress) {
          const p = res.progress as Record<string, unknown>
          const cur = Number(p.current ?? 0)
          const tot = Number(p.total ?? 1)
          updateItem(itemId, { progress: tot > 0 ? Math.round((cur / tot) * 100) : 0 })
        }
        pollersRef.current[taskId] = window.setTimeout(poll, 1500)
      } catch (e) {
        if (e instanceof Error && e.message.includes('API 404')) {
          delete pollersRef.current[taskId]
          await loadList()
          return
        }
        pollersRef.current[taskId] = window.setTimeout(poll, 2500)
      }
    }
    poll()
  }, [loadList, updateItem])

  useEffect(() => {
    loadList()
    return () => clearPollers()
  }, [loadList, clearPollers])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(VECTOR_CATEGORY_STORAGE_KEY, activeCategory)
  }, [activeCategory])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(VECTOR_SELECTED_STORAGE_KEY, JSON.stringify(Array.from(selectedPaths)))
  }, [selectedPaths])

  useEffect(() => {
    setSelectedPaths(prev => {
      const selectable = new Set(items.filter(isSelectableForVector).map(it => it.file_path))
      let changed = false
      const next = new Set<string>()
      prev.forEach(path => {
        if (selectable.has(path)) {
          next.add(path)
        } else {
          changed = true
        }
      })
      return changed ? next : prev
    })
  }, [items])

  const handleStartOne = useCallback(async (item: LocalItem, force: boolean = false) => {
    const selectable = isSelectableForVector(item)
    const forceRerunnable = isForceRerunnableForVector(item)
    if (!selectable && !forceRerunnable) return

    if (force) {
      const ok = window.confirm(
        `确认强制重跑资料「${item.filename}」？\n将删除原有的 MongoDB / Milvus 向量化结果并重新处理，但保留 PDF 与 OCR 结果。`,
      )
      if (!ok) return
    }

    const previousState: Partial<LocalItem> = {
      status: item.status,
      progress: item.progress,
      error: item.error,
      taskId: item.taskId,
    }

    updateItem(item.id, { status: 'running', progress: 0, error: null, retry_count: 0 })
    setSelectedPaths(prev => {
      const next = new Set(prev)
      next.delete(item.file_path)
      return next
    })
    try {
      const res = await startVectorizeFromOcr(
        item.doc_path,
        item.category,
        item.filename.replace(/\.pdf$/i, ''),
        force,
      )
      updateItem(item.id, { taskId: res.task_id })
      pollTask(item.id, res.task_id)
    } catch (e) {
      if (force) {
        updateItem(item.id, previousState)
        await loadList()
        return
      }
      updateItem(item.id, { status: 'failed', error: e instanceof Error ? e.message : 'Start failed', progress: 0 })
    }
  }, [loadList, pollTask, updateItem])

  const handleStartSelected = useCallback(() => {
    const targets = Array.from(selectedPaths)
    targets.forEach(path => {
      const item = items.find(it => it.file_path === path)
      if (item && isSelectableForVector(item)) {
        void handleStartOne(item)
      }
    })
  }, [handleStartOne, items, selectedPaths])

  const selectablePaths = items
    .filter(isSelectableForVector)
    .map(it => it.file_path)
  const allSelectableSelected = selectablePaths.length > 0 && selectablePaths.every(path => selectedPaths.has(path))

  const toggleSelectAll = useCallback(() => {
    setSelectedPaths(prev => {
      const next = new Set(prev)
      if (allSelectableSelected) {
        selectablePaths.forEach(path => next.delete(path))
      } else {
        selectablePaths.forEach(path => next.add(path))
      }
      return next
    })
  }, [allSelectableSelected, selectablePaths])

  const toggleSelectOne = useCallback((path: string) => {
    setSelectedPaths(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  const totalCount = items.length
  const pendingCount = items.filter(it => it.status === 'pending' && it.can_vectorize).length
  const runningCount = items.filter(it => it.status === 'running' || it.status === 'waiting_network').length
  const completedCount = items.filter(it => it.status === 'completed').length
  const selectedCount = selectedPaths.size

  return (
    <div className="flex flex-col h-full space-y-4">
      <div className="flex-none">
        <h2 className="text-xl font-semibold text-gray-800">向量化处理</h2>
        <p className="text-sm text-gray-500 mt-1">从 OCR 结果自动分块、生成 Embedding 并写入 Milvus/MongoDB</p>
      </div>

      <div className="flex-none grid grid-cols-4 gap-4">
        {[
          { label: '总文档数', value: totalCount, color: 'text-gray-800', bg: 'bg-gray-50' },
          { label: '待开始', value: pendingCount, color: 'text-yellow-600', bg: 'bg-yellow-50' },
          { label: '进行中', value: runningCount, color: 'text-blue-600', bg: 'bg-blue-50' },
          { label: '已完成', value: completedCount, color: 'text-green-600', bg: 'bg-green-50' },
        ].map(({ label, value, color, bg }) => (
          <div key={label} className={`${bg} rounded-xl p-4 text-center border border-gray-100`}>
            <p className={`text-2xl font-semibold ${color}`}>{value}</p>
            <p className="text-xs text-gray-500 mt-1">{label}</p>
          </div>
        ))}
      </div>

      <div className="flex-none bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            {VECTOR_CATEGORIES.map(cat => (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={`px-4 py-2 rounded-lg text-sm transition-colors ${
                  activeCategory === cat
                    ? 'bg-primary-500 text-white font-medium'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">已选 {selectedCount}</span>
            <button
              onClick={loadList}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 disabled:opacity-50 transition-colors"
              title="刷新列表"
            >
              <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              onClick={handleStartSelected}
              disabled={selectedCount === 0}
              className="flex items-center gap-2 px-4 py-2 bg-accent-500 text-white rounded-lg text-sm hover:bg-accent-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Play size={16} />
              开始选中 {selectedCount > 0 ? `(${selectedCount})` : ''}
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        <div className="flex-none grid grid-cols-[50px_48px_1fr_90px_90px_130px_70px_120px] gap-2 px-5 py-3 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500">
          <span>编号</span>
          <span className="flex items-center justify-center">
            <input
              type="checkbox"
              checked={allSelectableSelected}
              onChange={toggleSelectAll}
              disabled={selectablePaths.length === 0}
              className="h-4 w-4 accent-primary-500"
              title="全选可向量化资料"
            />
          </span>
          <span>资料名称</span>
          <span>分类</span>
          <span>状态</span>
          <span>进度</span>
          <span className="text-center">Chunks</span>
          <span className="text-center">操作</span>
        </div>

        <div className="flex-1 overflow-y-auto">
          {items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-gray-400">
              <FileText size={40} strokeWidth={1} />
              <p className="mt-3 text-sm">暂无可向量化文档，请先在 OCR 页面完成识别</p>
              <p className="text-xs mt-1">仅展示 OCR 已完成的文档</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {items.map((item, idx) => {
                const selectable = isSelectableForVector(item)
                const forceRerunnable = isForceRerunnableForVector(item)
                return (
                  <div
                    key={item.file_path}
                    className="grid grid-cols-[50px_48px_1fr_90px_90px_130px_70px_120px] gap-2 px-5 py-2.5 items-center text-sm hover:bg-gray-50/50 transition-colors"
                  >
                    <span className="text-xs text-gray-400 font-mono">{idx + 1}</span>
                    <span className="flex items-center justify-center">
                      <input
                        type="checkbox"
                        checked={selectedPaths.has(item.file_path)}
                        onChange={() => toggleSelectOne(item.file_path)}
                        disabled={!selectable}
                        className="h-4 w-4 accent-primary-500 disabled:opacity-40 disabled:cursor-not-allowed"
                        title={selectable ? '选择该资料' : '仅待开始/失败且可向量化可勾选'}
                      />
                    </span>
                    <span className="text-gray-700 truncate" title={item.filename}>{item.filename}</span>
                    <span className="text-xs text-gray-500">{item.category}</span>
                    <StatusBadge status={item.status} module="vector" />

                    <div className="pr-2">
                      {item.status === 'running' ? (
                        <ProgressBar current={item.progress} total={100} />
                      ) : item.status === 'waiting_network' ? (
                        <span className="text-xs text-orange-600 truncate" title={item.error ?? ''}>
                          {getWaitingNetworkMessage(item.error, item.retry_count)}
                        </span>
                      ) : item.status === 'completed' ? (
                        <span className="text-xs text-green-600">100%</span>
                      ) : item.status === 'failed' ? (
                        <span className="text-xs text-red-500 truncate" title={item.error ?? ''}>
                          {item.error?.slice(0, 20) ?? '失败'}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400">--</span>
                      )}
                    </div>

                    <span className="text-center text-xs text-gray-600">
                      {item.status === 'completed' ? item.total_chunks : '--'}
                    </span>

                    <div className="flex items-center justify-center">
                      {selectable && (
                        <button
                          onClick={() => handleStartOne(item)}
                          className="p-1.5 rounded-md text-primary-500 hover:bg-primary-50 transition-colors"
                          title={item.status === 'failed' ? '重试向量化' : '开始向量化'}
                        >
                          <Play size={14} />
                        </button>
                      )}
                      {forceRerunnable && (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleStartOne(item, true)}
                            className="p-1.5 rounded-md text-primary-500 hover:bg-primary-50 transition-colors"
                            title="强制重跑向量化"
                          >
                            <RotateCcw size={14} />
                          </button>
                          <span className="inline-flex items-center gap-1 text-xs text-green-600">
                            <CheckCircle2 size={14} />
                            已完成
                          </span>
                        </div>
                      )}
                      {!selectable && item.status === 'pending' && !item.can_vectorize && (
                        <span className="text-xs text-gray-400">等待 OCR</span>
                      )}
                      {item.status === 'waiting_network' && (
                        <span className="text-xs text-orange-600">自动重试</span>
                      )}
                      {item.status === 'completed' && !forceRerunnable && (
                        <span className="inline-flex items-center gap-1 text-xs text-green-600">
                          <CheckCircle2 size={14} />
                          已完成
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
