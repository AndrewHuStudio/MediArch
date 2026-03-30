"use client"

import { useState } from "react"
import { FileText, ExternalLink } from 'lucide-react'
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import { useT } from "@/lib/i18n"

export interface PDFSource {
  id: string
  title: string
  pageNumber: number
  snippet: string
  highlightText?: string
  imageUrl?: string
  thumbnail?: string
  pdfUrl?: string
  documentPath?: string
  filePath?: string
  section?: string
  metadata?: Record<string, unknown>
  docId?: string
  contentType?: 'text' | 'image' | 'table'  // [FIX 2025-12-27] 添加 content_type 字段
  // PDF 高亮位置信息
  positions?: Array<{
    page: number
    bbox?: number[]
    x?: number
    y?: number
    width?: number
    height?: number
  }>
}

interface PDFSourceCardProps {
  source: PDFSource
  index: number
  onClick: () => void
}

export function PDFSourceCard({ source, index, onClick }: PDFSourceCardProps) {
  const [isHovered, setIsHovered] = useState(false)
  const { t } = useT()

  return (
    <motion.div
      initial={{ opacity: 0, x: 50, rotate: 8 }}
      animate={{
        opacity: 1,
        x: isHovered ? -20 : 0,
        rotate: isHovered ? 0 : 8,
        scale: isHovered ? 1.05 : 1,
        zIndex: isHovered ? 50 : 10 + index,
      }}
      transition={{
        type: "spring",
        stiffness: 300,
        damping: 25,
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={onClick}
      className={cn(
        "absolute cursor-pointer",
        "w-32 h-40 rounded-lg overflow-hidden",
        "bg-gradient-to-br from-gray-800/90 to-gray-900/90",
        "backdrop-blur-md border-2",
        isHovered ? "border-blue-400 shadow-2xl shadow-blue-500/50" : "border-gray-600/50 shadow-lg",
      )}
      style={{
        right: `${-40 + index * 8}px`,
        top: `${index * 12}px`,
        transformOrigin: "center right",
      }}
    >
      {/* PDF Thumbnail */}
      <div className="h-24 bg-gray-700/50 flex items-center justify-center border-b border-gray-600/50">
        {source.thumbnail ? (
          <img src={source.thumbnail || "/placeholder.svg"} alt={source.title} className="w-full h-full object-cover" />
        ) : (
          <FileText className="w-12 h-12 text-gray-400" />
        )}
      </div>

      {/* PDF Info */}
      <div className="p-2 space-y-1">
        <h4 className="text-xs font-semibold text-white truncate">{source.title}</h4>
        <p className="text-xs text-gray-400">{t('pdf.page', { n: source.pageNumber })}</p>
        {isHovered && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center gap-1 text-xs text-blue-400"
          >
            <ExternalLink className="w-3 h-3" />
            <span>{t('pdf.viewDetail')}</span>
          </motion.div>
        )}
      </div>

      {/* Hover Indicator */}
      {isHovered && (
        <motion.div
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-400 to-blue-600"
        />
      )}
    </motion.div>
  )
}
