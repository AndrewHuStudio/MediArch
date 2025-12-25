"use client"

import { useState } from "react"
import { FileText } from 'lucide-react'
import { motion, AnimatePresence } from "framer-motion"
import type { PDFSource } from "./pdf-source-card"

// 重新导出 PDFSource 类型以便其他文件导入
export type { PDFSource } from "./pdf-source-card"

interface PDFCitationBadgeProps {
  source: PDFSource
  citationNumber: number
  onClick: () => void
  style?: React.CSSProperties
}

export function PDFCitationBadge({ source, citationNumber, onClick, style }: PDFCitationBadgeProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <div
      className={`absolute pointer-events-auto ${isExpanded ? 'z-50' : 'z-10'}`}
      style={style}
    >
      <div 
        className="relative flex items-start group"
        onMouseEnter={() => setIsExpanded(true)}
        onMouseLeave={() => setIsExpanded(false)}
      >
        <motion.div
          className="w-7 h-7 flex items-center justify-center rounded-md cursor-pointer shadow-lg backdrop-blur-sm border border-gray-700/50 relative z-10 transition-opacity duration-300 group-hover:opacity-0"
          style={{
            background: 'linear-gradient(135deg, rgba(55, 65, 81, 0.95), rgba(31, 41, 55, 0.98))',
          }}
          whileHover={{ 
            scale: 1.05,
            boxShadow: '0 8px 16px rgba(0, 0, 0, 0.5)',
          }}
          onClick={onClick}
          transition={{ duration: 0.2 }}
        >
          <span className="text-[11px] font-semibold text-gray-200">
            {citationNumber}
          </span>
        </motion.div>

        <AnimatePresence>
          {isExpanded && (
            <motion.div
              initial={{ 
                width: 0, 
                opacity: 0,
                x: -8,
              }}
              animate={{ 
                width: 220, 
                opacity: 1,
                x: 0,
              }}
              exit={{ 
                width: 0, 
                opacity: 0,
                x: -8,
              }}
              transition={{ 
                duration: 0.3, 
                ease: [0.4, 0, 0.2, 1]
              }}
              className="absolute left-0 top-0 overflow-hidden cursor-pointer"
              style={{
                transformOrigin: 'left center',
              }}
              onClick={onClick}
            >
              <div className="w-[220px] bg-gradient-to-br from-gray-900/98 to-gray-800/98 backdrop-blur-lg border border-gray-700/60 rounded-lg shadow-2xl overflow-hidden">
                {/* 缩略图 */}
                {source.thumbnail ? (
                  <div className="w-full h-28 overflow-hidden bg-gray-800/50">
                    <img 
                      src={source.thumbnail || "/placeholder.svg"} 
                      alt={source.title} 
                      className="w-full h-full object-cover" 
                    />
                  </div>
                ) : (
                  <div className="w-full h-28 flex items-center justify-center bg-gradient-to-br from-gray-800/80 to-gray-900/80">
                    <FileText className="w-10 h-10 text-gray-600" />
                  </div>
                )}
                
                {/* 资料信息 */}
                <div className="p-3 space-y-1.5">
                  <div className="text-xs font-semibold text-gray-100 line-clamp-2 leading-tight" title={source.title}>
                    {source.title}
                  </div>
                  <div className="text-[10px] text-blue-400 font-medium">
                    第 {source.pageNumber} 页
                  </div>
                  <div className="text-[10px] text-gray-400 line-clamp-2 leading-relaxed">
                    {source.snippet}
                  </div>
                </div>

                {/* 点击提示 */}
                <div className="px-3 pb-2">
                  <div className="text-[9px] text-gray-500 text-center">
                    点击查看详情
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
