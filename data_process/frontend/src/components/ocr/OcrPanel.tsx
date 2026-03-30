import { useState, useCallback, useRef, useEffect } from 'react'
import { Upload, Play, FileText, Image, RefreshCw, Trash2 } from 'lucide-react'
import { fetchOcrList, uploadToDocuments, startOcr, getTaskStatus, deleteOcrFile } from '@/api/client'
import type { OcrListItem } from '@/api/client'
import { ProgressBar, StatusBadge } from '@/components/shared/ProgressBar'

const ALL_CATEGORY = '全部'
const DOC_CATEGORIES = ['标准规范', '参考论文', '书籍报告', '政策文件'] as const
const CATEGORIES = [ALL_CATEGORY, ...DOC_CATEGORIES] as const
const OCR_SELECTED_STORAGE_KEY = 'ocr:selectedPaths'
const OCR_CATEGORY_STORAGE_KEY = 'ocr:activeCategory'

interface LocalItem extends OcrListItem {
  progress: number
  taskId: string | null
  error: string | null
}

function isSelectableForOcr(status: LocalItem['status']) {
  return status === 'pending' || status === 'failed'
}

export function OcrPanel() {
  const [activeCategory, setActiveCategory] = useState<string>(() => {
    if (typeof window === 'undefined') return ALL_CATEGORY
    const saved = window.sessionStorage.getItem(OCR_CATEGORY_STORAGE_KEY)
    return saved && CATEGORIES.includes(saved as (typeof CATEGORIES)[number]) ? saved : ALL_CATEGORY
  })
  const [items, setItems] = useState<LocalItem[]>([])
  const [uploading, setUploading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() => {
    if (typeof window === 'undefined') return new Set()
    try {
      const raw = window.sessionStorage.getItem(OCR_SELECTED_STORAGE_KEY)
      const arr = raw ? JSON.parse(raw) : []
      if (!Array.isArray(arr)) return new Set()
      return new Set(arr.filter((v): v is string => typeof v === 'string' && v.length > 0))
    } catch {
      return new Set()
    }
  })
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 更新单条
  const updateItem = useCallback((id: number, patch: Partial<LocalItem>) => {
    setItems(prev => prev.map(it => it.id === id ? { ...it, ...patch } : it))
  }, [])

  // 从后端加载列表
  const loadList = useCallback(async () => {
    setRefreshing(true)
    try {
      const queryCategory = activeCategory === ALL_CATEGORY ? undefined : activeCategory
      const res = await fetchOcrList(queryCategory)
      const nextItems = res.items.map(item => ({ ...item, progress: item.status === 'completed' ? 100 : 0, taskId: null, error: null as string | null }))
      setItems(prev => {
        // 保留正在运行的本地状态（taskId / progress），合并后端数据
        const runningMap = new Map<string, LocalItem>()
        for (const it of prev) {
          if (it.status === 'running' && it.taskId) {
            runningMap.set(it.filename + '|' + it.category, it)
          }
        }
        return nextItems.map(item => {
          const key = item.filename + '|' + item.category
          const running = runningMap.get(key)
          if (running) {
            return { ...item, progress: running.progress, taskId: running.taskId, error: running.error, status: running.status }
          }
          return item
        })
      })
    } catch (e) {
      console.error('Failed to load OCR list:', e)
    } finally {
      setRefreshing(false)
    }
  }, [activeCategory])

  // 初始加载
  useEffect(() => { loadList() }, [loadList])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(OCR_CATEGORY_STORAGE_KEY, activeCategory)
  }, [activeCategory])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(OCR_SELECTED_STORAGE_KEY, JSON.stringify(Array.from(selectedPaths)))
  }, [selectedPaths])

  // 上传文件到 documents/{category}/
  const handleFilesSelected = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return
    if (activeCategory === ALL_CATEGORY) {
      window.alert('请先选择具体分类再上传文件')
      return
    }
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        if (!file.name.toLowerCase().endsWith('.pdf')) continue
        await uploadToDocuments(file, activeCategory)
      }
      // 上传完刷新列表
      await loadList()
    } catch (e) {
      console.error('Upload failed:', e)
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }, [activeCategory, loadList])

  // 轮询任务状态
  const pollTask = useCallback((itemId: number, taskId: string) => {
    const poll = async () => {
      try {
        const res = await getTaskStatus(taskId)
        if (res.status === 'completed') {
          // 完成后刷新列表拿到后端扫描的完整数据
          await loadList()
          return
        }
        if (res.status === 'failed') {
          updateItem(itemId, { status: 'failed', error: res.error ?? 'Unknown error', progress: 0 })
          return
        }
        if (res.progress) {
          const p = res.progress as Record<string, unknown>
          const cur = Number(p.current ?? 0)
          const tot = Number(p.total ?? 1)
          updateItem(itemId, { progress: tot > 0 ? Math.round((cur / tot) * 100) : 0 })
        }
        setTimeout(poll, 2000)
      } catch {
        setTimeout(poll, 3000)
      }
    }
    poll()
  }, [updateItem, loadList])

  // 开始单条 OCR
  const handleStartOne = useCallback(async (item: LocalItem) => {
    if (!item.file_path || item.status === 'running') return
    updateItem(item.id, { status: 'running', progress: 0, error: null })
    setSelectedPaths(prev => {
      const next = new Set(prev)
      next.delete(item.file_path)
      return next
    })
    try {
      const res = await startOcr(item.file_path, item.category)
      updateItem(item.id, { taskId: res.task_id })
      pollTask(item.id, res.task_id)
    } catch (e) {
      updateItem(item.id, { status: 'failed', error: e instanceof Error ? e.message : 'Start failed' })
    }
  }, [updateItem, pollTask])

  // 全部开始
  const handleStartAll = useCallback(() => {
    const targets = Array.from(selectedPaths)
    targets.forEach(filePath => {
      const item = items.find(it => it.file_path === filePath)
      if (item && isSelectableForOcr(item.status)) {
        handleStartOne(item)
        return
      }
      const cat = filePath.split('/')[0]
      if (!cat) return
      void startOcr(filePath, cat)
    })
    setSelectedPaths(prev => {
      const next = new Set(prev)
      targets.forEach(path => next.delete(path))
      return next
    })
  }, [items, handleStartOne, selectedPaths])

  const handleDeleteOne = useCallback(async (item: LocalItem) => {
    if (!item.file_path || item.status === 'running') return
    const ok = window.confirm(`确认删除资料「${item.filename}」？\n将同时删除 OCR 结果。`)
    if (!ok) return
    try {
      await deleteOcrFile(item.file_path, item.category)
      setSelectedPaths(prev => {
        const next = new Set(prev)
        next.delete(item.file_path)
        return next
      })
      await loadList()
    } catch (e) {
      console.error('Delete failed:', e)
    }
  }, [loadList])

  const selectablePaths = items
    .filter(it => isSelectableForOcr(it.status) && it.file_path)
    .map(it => it.file_path)
  const allSelectableSelected = selectablePaths.length > 0 && selectablePaths.every(p => selectedPaths.has(p))

  const toggleSelectAll = useCallback(() => {
    setSelectedPaths(prev => {
      const next = new Set(prev)
      if (allSelectableSelected) {
        selectablePaths.forEach(p => next.delete(p))
      } else {
        selectablePaths.forEach(p => next.add(p))
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

  // 统计
  const totalCount = items.length
  const completedCount = items.filter(it => it.status === 'completed').length
  const runningCount = items.filter(it => it.status === 'running').length
  const pendingCount = items.filter(it => it.status === 'pending').length
  const selectedCount = selectedPaths.size

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* 标题行 */}
      <div className="flex-none">
        <h2 className="text-xl font-semibold text-gray-800">OCR 文档识别</h2>
        <p className="text-sm text-gray-500 mt-1">选择分类，上传 PDF 文件，使用 MinerU 进行 OCR 识别</p>
      </div>

      {/* 统计卡片 */}
      <div className="flex-none grid grid-cols-4 gap-4">
        {[
          { label: '总文件数', value: totalCount, color: 'text-gray-800', bg: 'bg-gray-50' },
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

      {/* 分类选择 + 操作栏 */}
      <div className="flex-none bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            {CATEGORIES.map(cat => (
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
            <button
              onClick={loadList}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 disabled:opacity-50 transition-colors"
              title="刷新列表"
            >
              <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
              刷新
            </button>
            <label className="flex items-center gap-2 px-4 py-2 bg-primary-500 text-white rounded-lg text-sm cursor-pointer hover:bg-primary-600 transition-colors">
              <Upload size={16} />
              {uploading ? '上传中...' : (activeCategory === ALL_CATEGORY ? '先选分类上传' : '上传文件')}
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf"
                multiple
                className="hidden"
                disabled={uploading || activeCategory === ALL_CATEGORY}
                onChange={(e) => handleFilesSelected(e.target.files)}
              />
            </label>
            {(pendingCount > 0 || selectedCount > 0) && (
              <button
                onClick={handleStartAll}
                disabled={selectedCount === 0}
                className="flex items-center gap-2 px-4 py-2 bg-accent-500 text-white rounded-lg text-sm hover:bg-accent-600 transition-colors"
              >
                <Play size={16} />
                开始选中 {selectedCount > 0 ? `(${selectedCount})` : ''}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 文件列表 -- flex-1 撑满剩余高度 */}
      <div className="flex-1 min-h-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        {/* 表头 */}
        <div className="flex-none grid grid-cols-[50px_48px_1fr_90px_90px_130px_70px_70px_80px] gap-2 px-5 py-3 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500">
          <span>编号</span>
          <span className="flex items-center justify-center">
            <input
              type="checkbox"
              checked={allSelectableSelected}
              onChange={toggleSelectAll}
              disabled={selectablePaths.length === 0}
              className="h-4 w-4 accent-primary-500"
              title="全选待开始/失败资料"
            />
          </span>
          <span>资料名称</span>
          <span>分类</span>
          <span>状态</span>
          <span>进度</span>
          <span className="text-center">页数</span>
          <span className="text-center">图片</span>
          <span className="text-center">操作</span>
        </div>

        {/* 列表内容 -- 内部滚动 */}
        <div className="flex-1 overflow-y-auto">
          {items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-gray-400">
              <FileText size={40} strokeWidth={1} />
              <p className="mt-3 text-sm">暂无文件，请选择分类后上传 PDF</p>
              <p className="text-xs mt-1">文件存放于 data_process/documents/ 目录</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {items.map((item, idx) => (
                <div
                  key={`${item.category}-${item.filename}`}
                  className="grid grid-cols-[50px_48px_1fr_90px_90px_130px_70px_70px_80px] gap-2 px-5 py-2.5 items-center text-sm hover:bg-gray-50/50 transition-colors"
                >
                  <span className="text-xs text-gray-400 font-mono">{idx + 1}</span>
                  <span className="flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={Boolean(item.file_path && selectedPaths.has(item.file_path))}
                      onChange={() => item.file_path && toggleSelectOne(item.file_path)}
                      disabled={!item.file_path || !isSelectableForOcr(item.status)}
                      className="h-4 w-4 accent-primary-500 disabled:opacity-40 disabled:cursor-not-allowed"
                      title={isSelectableForOcr(item.status) ? '选择该资料' : '仅待开始/失败可勾选'}
                    />
                  </span>

                  <span className="text-gray-700 truncate" title={item.filename}>
                    {item.filename}
                  </span>

                  <span className="text-xs text-gray-500">{item.category}</span>

                  <StatusBadge status={item.status} module="ocr" />

                  <div className="pr-2">
                    {item.status === 'running' ? (
                      <ProgressBar current={item.progress} total={100} />
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
                    {item.status === 'completed' ? `${item.success_pages}/${item.total_pages}` : '--'}
                  </span>

                  <span className="text-center text-xs text-gray-600 flex items-center justify-center gap-1">
                    {item.status === 'completed' ? (
                      <><Image size={12} className="text-gray-400" />{item.image_count}</>
                    ) : '--'}
                  </span>

                  <div className="flex items-center justify-center gap-1">
                    {isSelectableForOcr(item.status) ? (
                      <button
                        onClick={() => handleStartOne(item)}
                        disabled={!item.file_path || !selectedPaths.has(item.file_path)}
                        className="p-1.5 rounded-md text-primary-500 hover:bg-primary-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        title={selectedPaths.has(item.file_path) ? (item.status === 'failed' ? '重新开始 OCR' : '开始 OCR') : '请先勾选后开始'}
                      >
                        <Play size={14} />
                      </button>
                    ) : (
                      <span className="w-[30px]"></span>
                    )}
                    <button
                      onClick={() => handleDeleteOne(item)}
                      disabled={item.status === 'running'}
                      className="p-1.5 rounded-md text-red-500 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      title={item.status === 'running' ? '运行中不可删除' : '删除资料'}
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
