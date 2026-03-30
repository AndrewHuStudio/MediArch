import { useState, useCallback, useEffect } from 'react'
import { ChevronRight, RotateCcw } from 'lucide-react'
import { startKgBuild } from '@/api/client'
import { useTask } from '@/hooks/useTask'
import { StatusBadge } from '@/components/shared/ProgressBar'
import { StrategySelector } from './StrategySelector'
import { ActionButtons } from './ActionButtons'
import { BuildHistory } from './BuildHistory'
import { KgBuildProgressCard } from './KgBuildProgressCard'
import './KgPanel.css'

const STAGES = [
  { key: 'ea_recognition', label: 'E-A 识别', desc: '多阶段渐进式实体-属性抽取' },
  { key: 'relation_extraction', label: '关系抽取', desc: '多阶段渐进式三元组抽取' },
  { key: 'triplet_optimization', label: '三元组优化', desc: '实体标准化 + 关系归一化 + 验证' },
  { key: 'cross_document_fusion', label: '跨文档融合', desc: '去重 + 潜在三元组识别 + Neo4j 写入' },
]

const KG_PARAMS_STORAGE_KEY = 'kg:buildParams'
const KG_TASK_STORAGE_KEY = 'kg:lastTaskId'
const STRATEGY_DEFAULTS: Record<string, { eaMaxRounds: number; eaThreshold: number; relMaxRounds: number; relThreshold: number }> = {
  B0: { eaMaxRounds: 1, eaThreshold: 3, relMaxRounds: 1, relThreshold: 2 },
  B1: { eaMaxRounds: 1, eaThreshold: 3, relMaxRounds: 1, relThreshold: 2 },
  B2: { eaMaxRounds: 5, eaThreshold: 3, relMaxRounds: 4, relThreshold: 2 },
  B3: { eaMaxRounds: 5, eaThreshold: 3, relMaxRounds: 4, relThreshold: 2 },
}

function loadSavedParams() {
  if (typeof window === 'undefined') {
    return { eaMaxRounds: 5, eaThreshold: 3, relMaxRounds: 4, relThreshold: 2 }
  }
  try {
    const raw = window.sessionStorage.getItem(KG_PARAMS_STORAGE_KEY)
    if (!raw) {
      return { eaMaxRounds: 5, eaThreshold: 3, relMaxRounds: 4, relThreshold: 2 }
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>
    return {
      eaMaxRounds: Number(parsed.eaMaxRounds ?? 5),
      eaThreshold: Number(parsed.eaThreshold ?? 3),
      relMaxRounds: Number(parsed.relMaxRounds ?? 4),
      relThreshold: Number(parsed.relThreshold ?? 2),
    }
  } catch {
    return { eaMaxRounds: 5, eaThreshold: 3, relMaxRounds: 4, relThreshold: 2 }
  }
}

export function KgPanel() {
  const saved = loadSavedParams()
  const [eaMaxRounds, setEaMaxRounds] = useState(saved.eaMaxRounds)
  const [eaThreshold, setEaThreshold] = useState(saved.eaThreshold)
  const [relMaxRounds, setRelMaxRounds] = useState(saved.relMaxRounds)
  const [relThreshold, setRelThreshold] = useState(saved.relThreshold)
  const [kgResult, setKgResult] = useState<Record<string, unknown> | null>(null)

  // 新增: 策略选择和历史记录
  const [selectedStrategy, setSelectedStrategy] = useState('B1')
  const [buildHistory, setBuildHistory] = useState<any[]>([])
  const [showHistory, setShowHistory] = useState(false)

  const task = useTask({ persistKey: KG_TASK_STORAGE_KEY })

  // 加载构建历史
  const loadHistory = useCallback(async () => {
    try {
      const response = await fetch('/data-process/kg/history')
      const data = await response.json()
      setBuildHistory(data.builds || [])
    } catch (error) {
      console.error('Failed to load history:', error)
    }
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  useEffect(() => {
    const defaults = STRATEGY_DEFAULTS[selectedStrategy]
    if (!defaults) return
    setEaMaxRounds(defaults.eaMaxRounds)
    setEaThreshold(defaults.eaThreshold)
    setRelMaxRounds(defaults.relMaxRounds)
    setRelThreshold(defaults.relThreshold)
  }, [selectedStrategy])

  const handleBuild = useCallback(() => {
    setKgResult(null)
    task.start(() =>
      startKgBuild({
        source: 'mongodb',
        strategy: selectedStrategy,
        ea_max_rounds: eaMaxRounds,
        ea_threshold: eaThreshold,
        rel_max_rounds: relMaxRounds,
        rel_threshold: relThreshold,
        save_to_history: true,
      })
    )
  }, [eaMaxRounds, eaThreshold, relMaxRounds, relThreshold, selectedStrategy, task])

  const handleClearNeo4j = useCallback(async () => {
    if (!confirm(
      '确定要保留骨架清空当前图谱吗？\n\n- 会删除本次构建生成的节点及其关联关系\n- 会保留骨架/概念节点与骨架关系\n- 会清除 MongoDB 中的 KG 处理标记'
    )) {
      return
    }
    try {
      const response = await fetch('/data-process/kg/neo4j/clear', { method: 'DELETE' })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const data = await response.json()
      const deletedNodes = Number(data?.neo4j?.deleted_nodes ?? 0)
      const keptSkeleton = Number(data?.neo4j?.preserved_skeleton_nodes ?? 0)
      const clearedFlags = Number(data?.mongodb?.processed_chunks_cleared ?? 0)
      alert(`已完成保留骨架清空\n- 删除构建节点: ${deletedNodes}\n- 保留骨架节点: ${keptSkeleton}\n- 清除处理标记: ${clearedFlags}`)
      loadHistory()
    } catch (error) {
      alert('清空失败: ' + error)
    }
  }, [loadHistory])

  const handleDeleteHistory = useCallback(async (buildId: string) => {
    try {
      await fetch(`/data-process/kg/history/${buildId}`, { method: 'DELETE' })
      loadHistory()
    } catch (error) {
      alert('删除失败: ' + error)
    }
  }, [loadHistory])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(
      KG_PARAMS_STORAGE_KEY,
      JSON.stringify({ eaMaxRounds, eaThreshold, relMaxRounds, relThreshold }),
    )
  }, [eaMaxRounds, eaThreshold, relMaxRounds, relThreshold])

  useEffect(() => {
    if (task.state === 'completed' && task.result) {
      setKgResult(task.result)
    }
  }, [task.result, task.state])

  // 解析当前进度所在阶段
  const currentStage = task.progress?.stage?.split(':')?.[0] ?? ''
  const currentStageIdx = STAGES.findIndex((s) => currentStage.includes(s.key))

  const stages = (kgResult?.stages as Array<Record<string, unknown>>) ?? []

  return (
    <div className="flex flex-col h-full space-y-6 overflow-y-auto">
      <div className="flex-none">
        <h2 className="text-xl font-semibold text-gray-800">知识图谱构建</h2>
        <p className="text-sm text-gray-500 mt-1">
          多阶段渐进式知识抽取与图谱构建
        </p>
      </div>

      {/* 策略选择器 */}
      <div className="flex-none bg-white rounded-xl border border-gray-200 p-6">
        <StrategySelector value={selectedStrategy} onChange={setSelectedStrategy} />
      </div>

      {/* 参数配置 */}
      <div className="flex-none bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="text-sm font-medium text-gray-700 mb-4">参数配置</h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">E-A 最大轮数</label>
            <input
              type="number"
              value={eaMaxRounds}
              onChange={(e) => setEaMaxRounds(Number(e.target.value))}
              min={1}
              max={10}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-300"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">E-A 收敛阈值</label>
            <input
              type="number"
              value={eaThreshold}
              onChange={(e) => setEaThreshold(Number(e.target.value))}
              min={1}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-300"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">关系最大轮数</label>
            <input
              type="number"
              value={relMaxRounds}
              onChange={(e) => setRelMaxRounds(Number(e.target.value))}
              min={1}
              max={10}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-300"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">关系收敛阈值</label>
            <input
              type="number"
              value={relThreshold}
              onChange={(e) => setRelThreshold(Number(e.target.value))}
              min={1}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-300"
            />
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="mt-5">
          <ActionButtons
            isBuilding={task.state === 'running' || task.state === 'pending'}
            onBuild={handleBuild}
            onClearNeo4j={handleClearNeo4j}
            onViewHistory={() => setShowHistory(!showHistory)}
          />
        </div>

        <div className="flex items-center gap-3 mt-3">
          {(task.state === 'completed' || task.state === 'failed') && (
            <button
              onClick={() => {
                task.reset()
                setKgResult(null)
              }}
              className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 transition-colors"
            >
              <RotateCcw size={14} />
              重置
            </button>
          )}
          <StatusBadge status={task.state} />
        </div>
      </div>

      {/* 构建历史 */}
      {showHistory && (
        <div className="flex-none bg-white rounded-xl border border-gray-200 p-6">
          <BuildHistory
            records={buildHistory}
            onDelete={handleDeleteHistory}
            onRefresh={loadHistory}
          />
        </div>
      )}

      {/* 进度区与 4 阶段步骤条 */}
      {(task.state === 'running' || task.state === 'completed' || task.state === 'failed') && (
        <div className="flex-none space-y-4">
          <KgBuildProgressCard
            state={task.state}
            progress={task.progress}
            createdAt={task.createdAt}
          />

          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h3 className="text-sm font-medium text-gray-700">处理阶段</h3>
                <p className="text-xs text-gray-400 mt-1">保留 4 阶段主流程，并展示各阶段产物与执行状态。</p>
              </div>
              {task.progress && task.state === 'running' && (
                <p className="text-xs font-medium text-primary-600">
                  当前: {task.progress.stage || '准备构建'}
                </p>
              )}
            </div>
          <div className="space-y-3">
            {STAGES.map((stage, idx) => {
              const isActive = idx === currentStageIdx && task.state === 'running'
              const isDone = kgResult
                ? idx < stages.length
                : idx < currentStageIdx
              const stageData = stages[idx]

              return (
                <div
                  key={stage.key}
                  className={`flex items-center gap-4 p-4 rounded-lg border transition-colors ${
                    isActive
                      ? 'border-primary-300 bg-primary-50'
                      : isDone
                      ? 'border-green-200 bg-green-50/50'
                      : 'border-gray-100 bg-gray-50/50'
                  }`}
                >
                  {/* 序号 */}
                  <div
                    className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                      isActive
                        ? 'bg-primary-500 text-white'
                        : isDone
                        ? 'bg-green-500 text-white'
                        : 'bg-gray-200 text-gray-500'
                    }`}
                  >
                    {isDone ? '\u2713' : idx + 1}
                  </div>

                  {/* 信息 */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-gray-800">{stage.label}</p>
                      {isActive && (
                        <span className="inline-flex items-center rounded-full bg-primary-100 px-2 py-0.5 text-[11px] font-medium text-primary-700">
                          运行中
                        </span>
                      )}
                      {isDone && !isActive && (
                        <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-700">
                          已完成
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-400">{stage.desc}</p>
                  </div>

                  {/* 阶段统计 */}
                  {stageData && (
                    <div className="text-xs text-gray-500 text-right space-y-0.5">
                      {stageData.ea_pairs_count != null && (
                        <p>实体: {String(stageData.ea_pairs_count)}</p>
                      )}
                      {stageData.triplets_count != null && (
                        <p>三元组: {String(stageData.triplets_count)}</p>
                      )}
                      {stageData.stats && typeof stageData.stats === 'object' ? (
                        <p className="text-gray-400">
                          {Object.entries(stageData.stats as Record<string, unknown>)
                            .slice(0, 2)
                            .map(([k, v]) => `${k}: ${String(v)}`)
                            .join(' | ')}
                        </p>
                      ) : null}
                    </div>
                  )}

                  {isActive && <ChevronRight size={16} className="text-primary-400 animate-pulse" />}
                </div>
              )
            })}
          </div>

          {task.error && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {task.error}
            </div>
          )}
        </div>
        </div>
      )}

      {/* 最终结果 */}
      {kgResult && (
        <div className="flex-none bg-white rounded-xl border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-700 mb-4">构建结果</h3>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
            {[
              { label: '实体数', value: kgResult.total_entities, color: 'text-blue-600' },
              { label: '关系类型', value: kgResult.total_relations, color: 'text-purple-600' },
              { label: '三元组', value: kgResult.total_triplets, color: 'text-orange-600' },
              { label: 'Neo4j 节点', value: kgResult.nodes_written, color: 'text-green-600' },
              { label: 'Neo4j 边', value: kgResult.edges_written, color: 'text-teal-600' },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-gray-50 rounded-lg p-4 text-center">
                <p className={`text-2xl font-semibold ${color}`}>{String(value ?? 0)}</p>
                <p className="text-xs text-gray-500 mt-1">{label}</p>
              </div>
            ))}
          </div>

          {/* 融合统计 */}
          {kgResult.fusion_stats && typeof kgResult.fusion_stats === 'object' ? (
            <div className="mt-4 p-4 bg-gray-50 rounded-lg">
              <p className="text-xs font-medium text-gray-600 mb-2">融合统计</p>
              <div className="flex flex-wrap gap-4 text-xs text-gray-500">
                {Object.entries(kgResult.fusion_stats as Record<string, unknown>).map(([k, v]) => (
                  <span key={k}>
                    {k}: <span className="font-medium text-gray-700">{String(v)}</span>
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
