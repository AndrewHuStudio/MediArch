/** 进度条组件 */
export function ProgressBar({ current, total, label }: { current: number; total: number; label?: string }) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0
  return (
    <div className="w-full">
      {label && <p className="text-xs text-gray-500 mb-1">{label}</p>}
      <div className="w-full bg-gray-200 rounded-full h-2.5">
        <div
          className="bg-primary-500 h-2.5 rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-gray-400 mt-1 text-right">{pct}%</p>
    </div>
  )
}

/** 状态徽章 */
export function StatusBadge({ status, module }: { status: string; module?: 'ocr' | 'vector' | 'kg' }) {
  const styles: Record<string, string> = {
    idle: 'bg-gray-100 text-gray-600',
    pending: 'bg-yellow-100 text-yellow-700',
    running: 'bg-blue-100 text-blue-700',
    waiting_network: 'bg-orange-100 text-orange-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
  }
  const labels: Record<string, string> = {
    idle: '待处理',
    pending: '待开始',
    running: '进行中',
    waiting_network: '等待网络',
    completed: module === 'vector' ? '已向量化' : '已完成',
    failed: '失败',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? styles.idle}`}>
      {labels[status] ?? status}
    </span>
  )
}
