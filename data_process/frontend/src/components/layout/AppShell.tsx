import { NavLink } from 'react-router-dom'
import { FileText, Database, GitBranch, LayoutDashboard } from 'lucide-react'
import type { ReactNode } from 'react'
import { getDataProcessApiLabel } from '@/config/apiTarget'

const navItems = [
  { to: '/overview', label: '构建总览', icon: LayoutDashboard },
  { to: '/ocr', label: 'OCR 识别', icon: FileText },
  { to: '/vector', label: '向量化', icon: Database },
  { to: '/kg', label: '知识图谱', icon: GitBranch },
]

export function AppShell({ children }: { children: ReactNode }) {
  const apiLabel = getDataProcessApiLabel(import.meta.env.VITE_DATA_PROCESS_API_TARGET)

  return (
    <div className="flex h-screen">
      {/* 侧边栏 */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="px-5 py-5 border-b border-gray-100">
          <h1 className="text-lg font-semibold text-gray-800">MediArch</h1>
          <p className="text-xs text-gray-400 mt-0.5">数据处理平台</p>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-primary-50 text-primary-700 font-medium'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                }`
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
          API: {apiLabel}
        </div>
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 overflow-hidden bg-gray-50 p-6 flex flex-col">
        <div className="flex-1 min-h-0">
          {children}
        </div>
      </main>
    </div>
  )
}
