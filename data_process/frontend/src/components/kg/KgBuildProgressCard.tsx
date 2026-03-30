import { getKgProgressSnapshot } from './progressUtils'

type TaskState = 'idle' | 'pending' | 'running' | 'completed' | 'failed'

type TaskProgress = {
  stage: string
  current: number
  total: number
  message: string
} | null

interface KgBuildProgressCardProps {
  state: TaskState
  progress: TaskProgress
  createdAt?: string | null
}

export function KgBuildProgressCard({ state, progress, createdAt }: KgBuildProgressCardProps) {
  const snapshot = getKgProgressSnapshot({ state, progress, createdAt })

  const overallBarClass = state === 'failed'
    ? 'bg-red-500'
    : state === 'completed'
    ? 'bg-green-500'
    : 'bg-primary-500'

  const stageBarClass = state === 'failed'
    ? 'bg-red-400'
    : state === 'completed'
    ? 'bg-green-400'
    : 'bg-primary-300'

  return (
    <div className="rounded-2xl border border-primary-100 bg-gradient-to-br from-primary-50 via-white to-white p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-primary-500">构建进度</p>
          <div className="mt-2 flex items-end gap-3">
            <span className="text-4xl font-semibold text-gray-900">{snapshot.overallPercent}%</span>
            <div className="pb-1">
              <p className="text-sm font-medium text-gray-800">{snapshot.stageLabel}</p>
              <p className="text-xs text-gray-500">{snapshot.stepLabel}</p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div className="rounded-xl border border-white/80 bg-white/80 px-4 py-3">
            <p className="text-xs text-gray-500">当前阶段</p>
            <p className="mt-1 font-medium text-gray-800">{snapshot.stagePercent}%</p>
          </div>
          <div className="rounded-xl border border-white/80 bg-white/80 px-4 py-3">
            <p className="text-xs text-gray-500">已耗时</p>
            <p className="mt-1 font-medium text-gray-800">{snapshot.elapsedLabel}</p>
          </div>
          <div className="rounded-xl border border-white/80 bg-white/80 px-4 py-3">
            <p className="text-xs text-gray-500">预计剩余</p>
            <p className="mt-1 font-medium text-gray-800">{snapshot.etaLabel}</p>
          </div>
          <div className="rounded-xl border border-white/80 bg-white/80 px-4 py-3">
            <p className="text-xs text-gray-500">预计总耗时</p>
            <p className="mt-1 font-medium text-gray-800">{snapshot.estimatedTotalLabel}</p>
          </div>
        </div>
      </div>

      <div className="mt-5 space-y-3">
        <div>
          <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
            <span>整体进度</span>
            <span>{snapshot.overallPercent}%</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-white/90 ring-1 ring-primary-100">
            <div
              className={`h-full rounded-full transition-all duration-500 ${overallBarClass}`}
              style={{ width: `${snapshot.overallPercent}%` }}
            />
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
            <span>当前阶段推进</span>
            <span>{snapshot.current}/{snapshot.total || 0}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className={`h-full rounded-full transition-all duration-500 ${stageBarClass}`}
              style={{ width: `${snapshot.stagePercent}%` }}
            />
          </div>
        </div>

        <div className="flex flex-col gap-2 text-xs text-gray-500 sm:flex-row sm:items-center sm:justify-between">
          <p>{snapshot.detailMessage || `${snapshot.stepLabel}，界面将持续按阶段推进更新。`}</p>
          <p>{snapshot.estimateSourceLabel}</p>
        </div>

        <div className="flex flex-col gap-2 text-xs text-gray-500 sm:flex-row sm:items-center sm:justify-between">
          <p>
            {snapshot.totalChunks > 0 ? `本次构建共 ${snapshot.totalChunks} 个 chunks` : 'Chunk 总量读取中'}
          </p>
          <p>{state === 'running' ? '任务运行中' : state === 'completed' ? '任务已完成' : state === 'failed' ? '任务失败' : '等待任务启动'}</p>
        </div>
      </div>
    </div>
  )
}
