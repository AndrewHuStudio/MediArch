import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, Trash2 } from 'lucide-react'
import { deleteOcrFile, fetchPipelineOverview } from '@/api/client'
import type { PipelineOverviewResponse } from '@/api/client'
import { StatusBadge } from '@/components/shared/ProgressBar'

type OverviewFilter = 'all' | 'completed' | 'incomplete' | 'running' | 'failed'

export function OverviewPanel() {
  const [data, setData] = useState<PipelineOverviewResponse | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [filter, setFilter] = useState<OverviewFilter>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [deletingDocPath, setDeletingDocPath] = useState<string | null>(null)
  const [selectedDocPaths, setSelectedDocPaths] = useState<Set<string>>(new Set())
  const [sortBy, setSortBy] = useState<'default' | 'category' | 'ocr' | 'vector' | 'graph'>('default')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')

  const loadOverview = useCallback(async () => {
    setRefreshing(true)
    try {
      setData(await fetchPipelineOverview())
    } catch (e) {
      console.error('Failed to load pipeline overview:', e)
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void loadOverview()
    const timer = window.setInterval(() => { void loadOverview() }, 4000)
    return () => window.clearInterval(timer)
  }, [loadOverview])

  const summary = data?.summary
  const uploadedTotal = summary?.uploaded_total ?? 0
  const ocrCompleted = summary?.ocr_completed ?? 0
  const vectorCompleted = summary?.vector_completed ?? 0
  const kg = summary?.kg

  const ocrPct = useMemo(
    () => uploadedTotal > 0 ? Math.round((ocrCompleted / uploadedTotal) * 100) : 0,
    [ocrCompleted, uploadedTotal],
  )
  const vectorPct = useMemo(
    () => uploadedTotal > 0 ? Math.round((vectorCompleted / uploadedTotal) * 100) : 0,
    [vectorCompleted, uploadedTotal],
  )
  const graphStatusForFilter = useCallback((item: PipelineOverviewResponse['items'][number]) => {
    if (!item.can_graphize) return 'idle'
    if (kg?.status === 'running') return 'running'
    if (kg?.status === 'completed') return 'completed'
    if (kg?.status === 'failed') return 'failed'
    return 'pending'
  }, [kg?.status])
  const filteredItems = useMemo(() => {
    const items = (data?.items ?? []).filter((item) => categoryFilter === 'all' || item.category === categoryFilter)
    if (filter === 'all') return items
    if (filter === 'completed') {
      return items.filter((item) => item.ocr_status === 'completed' && item.vector_status === 'completed')
    }
    if (filter === 'failed') {
      return items.filter((item) => item.ocr_status === 'failed' || item.vector_status === 'failed')
    }
    if (filter === 'running') {
      return items.filter(
        (item) =>
          item.ocr_status === 'running'
          || item.vector_status === 'running'
          || item.vector_status === 'waiting_network'
          || graphStatusForFilter(item) === 'running',
      )
    }
    return items.filter((item) => !(item.ocr_status === 'completed' && item.vector_status === 'completed'))
  }, [categoryFilter, data?.items, filter, graphStatusForFilter])
  const categories = useMemo(() => {
    const set = new Set<string>((data?.items ?? []).map((item) => item.category))
    return ['all', ...Array.from(set)]
  }, [data?.items])
  const sortedItems = useMemo(() => {
    const rankStatus = (item: (typeof filteredItems)[number]) => {
      const isCompleted = item.ocr_status === 'completed' && item.vector_status === 'completed'
      const isRunning = item.ocr_status === 'running' || item.vector_status === 'running' || item.vector_status === 'waiting_network'
      const isFailed = item.ocr_status === 'failed' || item.vector_status === 'failed'
      if (isCompleted) return 4
      if (isRunning) return 3
      if (isFailed) return 2
      return 1
    }
    return [...filteredItems].sort((a, b) => {
      const diff = rankStatus(b) - rankStatus(a)
      if (diff !== 0) return diff
      return a.filename.localeCompare(b.filename, 'zh-CN')
    })
  }, [filteredItems])
  const visibleSelectableDocPaths = useMemo(
    () => sortedItems
      .filter((item) => item.ocr_status !== 'running' && item.vector_status !== 'running')
      .map((item) => item.doc_path),
    [sortedItems],
  )
  const allVisibleSelected = visibleSelectableDocPaths.length > 0
    && visibleSelectableDocPaths.every((docPath) => selectedDocPaths.has(docPath))

  const handleDelete = useCallback(async (docPath: string, category: string, filename: string) => {
    const ok = window.confirm(`确认删除资料「${filename}」？\n将同时删除 OCR 结果。`)
    if (!ok) return
    setDeletingDocPath(docPath)
    try {
      await deleteOcrFile(docPath, category)
      setSelectedDocPaths((prev) => {
        const next = new Set(prev)
        next.delete(docPath)
        return next
      })
      await loadOverview()
    } catch (e) {
      console.error('Delete failed:', e)
    } finally {
      setDeletingDocPath(null)
    }
  }, [loadOverview])

  const toggleSelectOne = useCallback((docPath: string) => {
    setSelectedDocPaths((prev) => {
      const next = new Set(prev)
      if (next.has(docPath)) next.delete(docPath)
      else next.add(docPath)
      return next
    })
  }, [])

  const toggleSelectAllVisible = useCallback(() => {
    setSelectedDocPaths((prev) => {
      const next = new Set(prev)
      if (allVisibleSelected) {
        visibleSelectableDocPaths.forEach((docPath) => next.delete(docPath))
      } else {
        visibleSelectableDocPaths.forEach((docPath) => next.add(docPath))
      }
      return next
    })
  }, [allVisibleSelected, visibleSelectableDocPaths])

  const handleDeleteSelected = useCallback(async () => {
    const targets = sortedItems.filter(
      (item) =>
        selectedDocPaths.has(item.doc_path)
        && item.ocr_status !== 'running'
        && item.vector_status !== 'running',
    )
    if (targets.length === 0) return
    const ok = window.confirm(`确认删除选中的 ${targets.length} 条资料？\n将同时删除 OCR 结果。`)
    if (!ok) return

    try {
      for (const item of targets) {
        setDeletingDocPath(item.doc_path)
        // eslint-disable-next-line no-await-in-loop
        await deleteOcrFile(item.doc_path, item.category)
      }
      setSelectedDocPaths((prev) => {
        const next = new Set(prev)
        targets.forEach((item) => next.delete(item.doc_path))
        return next
      })
      await loadOverview()
    } catch (e) {
      console.error('Batch delete failed:', e)
    } finally {
      setDeletingDocPath(null)
    }
  }, [loadOverview, selectedDocPaths, sortedItems])

  const handleSort = useCallback((column: 'category' | 'ocr' | 'vector' | 'graph') => {
    setSortBy((prev) => {
      if (prev === column) {
        setSortOrder((o) => (o === 'desc' ? 'asc' : 'desc'))
        return prev
      }
      setSortOrder('desc')
      return column
    })
  }, [])

  const graphStatusOf = useCallback((item: (typeof sortedItems)[number]) => {
    if (!item.can_graphize) return 'idle'
    if (kg?.status === 'running') return 'running'
    if (kg?.status === 'completed') return 'completed'
    if (kg?.status === 'failed') return 'failed'
    return 'pending'
  }, [kg?.status])

  const displayedItems = useMemo(() => {
    if (sortBy === 'default') return sortedItems
    const statusRank: Record<string, number> = {
      completed: 4,
      running: 3,
      failed: 2,
      pending: 1,
      idle: 0,
    }
    const dir = sortOrder === 'desc' ? -1 : 1
    const arr = [...sortedItems]
    arr.sort((a, b) => {
      if (sortBy === 'category') {
        return dir * a.category.localeCompare(b.category, 'zh-CN')
      }
      if (sortBy === 'ocr') {
        return dir * (statusRank[a.ocr_status] - statusRank[b.ocr_status])
      }
      if (sortBy === 'vector') {
        return dir * (statusRank[a.vector_status] - statusRank[b.vector_status])
      }
      const ga = graphStatusOf(a)
      const gb = graphStatusOf(b)
      return dir * (statusRank[ga] - statusRank[gb])
    })
    return arr
  }, [graphStatusOf, sortBy, sortOrder, sortedItems])

  return (
    <div className="flex flex-col h-full space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-800">构建总览</h2>
          <p className="text-sm text-gray-500 mt-1">一屏查看上传、OCR、向量化、图谱构建进度</p>
        </div>
        <button
          onClick={loadOverview}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 disabled:opacity-50 transition-colors"
          title="刷新总览"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">已上传资料</p>
          <p className="text-2xl font-semibold text-gray-800 mt-1">{uploadedTotal}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">OCR 完成</p>
          <p className="text-2xl font-semibold text-blue-600 mt-1">{ocrCompleted}</p>
          <p className="text-xs text-gray-400 mt-1">完成率 {ocrPct}%</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">向量化完成</p>
          <p className="text-2xl font-semibold text-green-600 mt-1">{vectorCompleted}</p>
          <p className="text-xs text-gray-400 mt-1">完成率 {vectorPct}%</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">图谱构建</p>
          <div className="mt-1">
            <StatusBadge status={kg?.status ?? 'idle'} />
          </div>
          <p className="text-xs text-gray-400 mt-1">
            {kg?.status === 'running' ? `进度 ${kg.progress_percent}%` : (kg?.stage || '未开始')}
          </p>
        </div>
      </div>

      <div className="flex-1 min-h-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
          <div className="flex items-center gap-2">
            {[
              { key: 'all', label: '全部' },
              { key: 'completed', label: '已完成' },
              { key: 'incomplete', label: '未完成' },
              { key: 'running', label: '进行中' },
              { key: 'failed', label: '失败' },
            ].map((item) => (
              <button
                key={item.key}
                onClick={() => setFilter(item.key as OverviewFilter)}
                className={`px-3 py-1.5 rounded-lg text-xs transition-colors ${
                  filter === item.key
                    ? 'bg-primary-500 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {item.label}
              </button>
            ))}
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="ml-2 px-3 py-1.5 rounded-lg text-xs bg-gray-100 text-gray-600 border border-gray-200"
              title="按分类筛选"
            >
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {cat === 'all' ? '全部分类' : cat}
                </option>
              ))}
            </select>
            <button
              onClick={handleDeleteSelected}
              disabled={selectedDocPaths.size === 0}
              className="ml-2 px-3 py-1.5 rounded-lg text-xs bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              删除选中 {selectedDocPaths.size > 0 ? `(${selectedDocPaths.size})` : ''}
            </button>
          </div>
          <p className="text-xs text-gray-400">当前显示 {displayedItems.length} 条</p>
        </div>
        <div className="grid grid-cols-[56px_44px_1fr_90px_90px_90px_90px_70px] gap-2 px-5 py-3 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500">
          <span>编号</span>
          <span className="flex items-center justify-center">
            <input
              type="checkbox"
              checked={allVisibleSelected}
              onChange={toggleSelectAllVisible}
              disabled={visibleSelectableDocPaths.length === 0}
              className="h-4 w-4 accent-primary-500"
              title="全选当前列表可删除资料"
            />
          </span>
          <span>资料名称</span>
          <button onClick={() => handleSort('category')} className="text-left hover:text-gray-700">分类</button>
          <button onClick={() => handleSort('ocr')} className="text-left hover:text-gray-700">OCR</button>
          <button onClick={() => handleSort('vector')} className="text-left hover:text-gray-700">向量化</button>
          <button onClick={() => handleSort('graph')} className="text-left hover:text-gray-700">图谱化</button>
          <span className="text-center">操作</span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {!data || displayedItems.length === 0 ? (
            <div className="py-16 text-center text-sm text-gray-400">暂无资料，请先上传 PDF</div>
          ) : (
            <div className="divide-y divide-gray-100">
              {displayedItems.map((item, idx) => (
                <div
                  key={item.doc_path}
                  className="grid grid-cols-[56px_44px_1fr_90px_90px_90px_90px_70px] gap-2 px-5 py-2.5 items-center text-sm hover:bg-gray-50/50"
                >
                  <span className="text-xs text-gray-400 font-mono">{idx + 1}</span>
                  <span className="flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={selectedDocPaths.has(item.doc_path)}
                      onChange={() => toggleSelectOne(item.doc_path)}
                      disabled={item.ocr_status === 'running' || item.vector_status === 'running' || item.vector_status === 'waiting_network'}
                      className="h-4 w-4 accent-primary-500"
                      title={item.ocr_status === 'running' || item.vector_status === 'running' || item.vector_status === 'waiting_network' ? '运行中不可勾选' : '选择该资料'}
                    />
                  </span>
                  <span className="text-gray-700 truncate" title={item.filename}>{item.filename}</span>
                  <span className="text-xs text-gray-500">{item.category}</span>
                  <StatusBadge status={item.ocr_status} module="ocr" />
                  <StatusBadge status={item.vector_status} module="vector" />
                  <StatusBadge status={graphStatusOf(item)} />
                  <div className="flex items-center justify-center">
                    <button
                      onClick={() => handleDelete(item.doc_path, item.category, item.filename)}
                      disabled={deletingDocPath === item.doc_path || item.ocr_status === 'running' || item.vector_status === 'running' || item.vector_status === 'waiting_network'}
                      className="p-1.5 rounded-md text-red-500 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      title={item.ocr_status === 'running' || item.vector_status === 'running' || item.vector_status === 'waiting_network' ? '运行中不可删除' : '删除资料'}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
