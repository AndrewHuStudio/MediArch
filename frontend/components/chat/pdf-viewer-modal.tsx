"use client"

import { useEffect, useMemo, useState } from "react"
import { X, ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react'
import { motion, AnimatePresence } from "framer-motion"
import { Document, Page, pdfjs } from "react-pdf"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { PDFSource } from "./pdf-source-card"

// 使用本地打包的 worker，避免 CDN 被阻断/跨域导致 PDF 无法加载
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString()

interface PDFViewerModalProps {
  isOpen: boolean
  onClose: () => void
  source: PDFSource | null
}

const MIN_SCALE = 0.8
const MAX_SCALE = 2.0

export function PDFViewerModal({ isOpen, onClose, source }: PDFViewerModalProps) {
  const [scale, setScale] = useState(1.1)
  const [numPages, setNumPages] = useState<number | null>(null)
  const [currentPage, setCurrentPage] = useState<number>(source?.pageNumber || 1)

  // [FIX 2025-12-09] 修复 Hooks 顺序问题：将 useMemo 移到 early return 之前
  const highlightBoxes = useMemo(() => {
    if (!source || !source.positions || source.positions.length === 0) return []

    return source.positions
      .map((pos) => {
        if (!pos) return null
        const page = (pos as any).page ?? source.pageNumber ?? 1
        const bbox = (pos as any).bbox
        if (Array.isArray(bbox) && bbox.length === 4) {
          return {
            page,
            x0: bbox[0],
            y0: bbox[1],
            x1: bbox[2],
            y1: bbox[3],
          }
        }
        return null
      })
      .filter((box): box is { page: number; x0: number; y0: number; x1: number; y1: number } => Boolean(box))
  }, [source])

  useEffect(() => {
    if (source?.pageNumber) {
      setCurrentPage(source.pageNumber)
    }
  }, [source])

  // [FIX 2025-12-09] 将 early return 移到所有 Hooks 之后
  if (!source) return null

  const currentPageHighlights = highlightBoxes.filter((box) => box.page === currentPage)

  const zoom = (direction: "in" | "out") => {
    setScale((prev) => {
      const next = direction === "in" ? prev + 0.1 : prev - 0.1
      return Math.min(Math.max(next, MIN_SCALE), MAX_SCALE)
    })
  }

  const canPrev = currentPage > 1
  const canNext = numPages ? currentPage < numPages : true

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100]"
          />

          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed inset-4 md:inset-8 z-[101] flex flex-col bg-gray-900 rounded-xl border border-gray-700 shadow-2xl overflow-hidden"
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700 bg-gray-800/50">
              <div>
                <h3 className="text-lg font-semibold text-white">{source.title}</h3>
                <p className="text-sm text-gray-400">
                  第 {currentPage} 页 {source.section ? `· ${source.section}` : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-gray-400 hover:text-white hover:bg-gray-700"
                  onClick={() => zoom("out")}
                  disabled={scale <= MIN_SCALE}
                >
                  <ZoomOut className="w-4 h-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-gray-400 hover:text-white hover:bg-gray-700"
                  onClick={() => zoom("in")}
                  disabled={scale >= MAX_SCALE}
                >
                  <ZoomIn className="w-4 h-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={onClose}
                  className="text-gray-400 hover:text-white hover:bg-gray-700"
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </div>

            <div className="flex-1 overflow-auto bg-gray-800 p-4 md:p-8">
              {source.pdfUrl ? (
                <div className="flex flex-col items-center gap-4">
                  <div className="relative bg-white shadow-2xl rounded-lg overflow-hidden">
                    <Document
                      file={source.pdfUrl}
                      loading={<div className="p-8 text-gray-300">正在加载 PDF ...</div>}
                      error={
                        <div className="p-8 bg-white min-h-[700px] w-full max-w-4xl">
                          <div className="space-y-6">
                            {/* 文档标题 */}
                            <div className="border-b border-gray-200 pb-4">
                              <h2 className="text-2xl font-bold text-gray-900">{source.title}</h2>
                              <div className="flex items-center gap-3 mt-2 text-sm text-gray-600">
                                <span>第 {source.pageNumber} 页</span>
                                {source.section && (
                                  <>
                                    <span>·</span>
                                    <span>{source.section}</span>
                                  </>
                                )}
                              </div>
                            </div>

                            {/* 提示信息 */}
                            <div className="bg-blue-50 border-l-4 border-blue-400 p-4 rounded">
                              <p className="text-sm text-blue-800">
                                PDF 文件加载失败，以下是文档内容文本预览
                              </p>
                            </div>

                            {/* 高亮内容区域 */}
                            <div className="relative bg-yellow-50 border-l-4 border-yellow-500 rounded-lg p-6 shadow-sm">
                              {/* 脉冲动画点 */}
                              <div className="absolute -left-2 top-6 w-4 h-4 bg-yellow-500 rounded-full animate-pulse shadow-lg" />

                              {/* 高亮标签 */}
                              <div className="flex items-center gap-2 mb-3">
                                <div className="bg-yellow-500 text-white text-xs font-bold px-2 py-1 rounded">
                                  高亮引用
                                </div>
                                <span className="text-xs text-yellow-800">AI 自动定位的关键内容</span>
                              </div>

                              {/* 高亮文本 */}
                              <div className="text-gray-900 leading-relaxed text-base">
                                {source.highlightText || source.snippet}
                              </div>
                            </div>

                            {/* 完整文档内容 */}
                            <div className="space-y-3 text-gray-700 leading-relaxed">
                              <h3 className="text-lg font-semibold text-gray-900 mb-3">完整段落内容</h3>
                              <p className="text-sm">{source.snippet}</p>

                              {/* 补充说明 */}
                              <div className="mt-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
                                <p className="text-sm text-gray-600">
                                  本内容节选自《{source.title}》第 {source.pageNumber} 页。
                                  医疗建筑设计需要综合考虑功能分区、流线设计、感染控制等多个方面，
                                  确保建筑满足医疗功能需求的同时，为患者和医护人员提供安全、舒适的环境。
                                </p>
                              </div>
                            </div>

                            {/* 底部提示 */}
                            <div className="mt-6 pt-4 border-t border-gray-200">
                              <p className="text-xs text-gray-500 text-center">
                                提示：此为模拟展示效果。完整 PDF 查看需要后端服务支持。
                              </p>
                            </div>
                          </div>
                        </div>
                      }
                      onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                    >
                      <div className="relative">
                        <Page
                          pageNumber={currentPage}
                          scale={scale}
                          renderAnnotationLayer={false}
                          renderTextLayer={false}
                        />
                        {currentPageHighlights.map((box, idx) => (
                          <div
                            key={idx}
                            className="absolute border-2 border-yellow-400/80 bg-yellow-300/30 rounded-sm pointer-events-none"
                            style={{
                              left: `${box.x0 * 100}%`,
                              top: `${box.y0 * 100}%`,
                              width: `${(box.x1 - box.x0) * 100}%`,
                              height: `${(box.y1 - box.y0) * 100}%`,
                            }}
                          />
                        ))}
                      </div>
                    </Document>
                  </div>
                  <div className="text-xs text-gray-400 text-center">
                    AI 根据 OCR 坐标自动标记高亮区域，若定位不准可在 PDF 中搜索：
                    <span className="text-blue-300"> {source.highlightText || source.snippet}</span>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-center h-full">
                  <div className="bg-white shadow-2xl rounded-lg p-8 max-w-3xl w-full">
                    <div className="space-y-6">
                      {/* 文档标题 */}
                      <div className="border-b border-gray-200 pb-4">
                        <h2 className="text-2xl font-bold text-gray-900">{source.title}</h2>
                        <div className="flex items-center gap-3 mt-2 text-sm text-gray-600">
                          <span>第 {source.pageNumber} 页</span>
                          {source.section && (
                            <>
                              <span>·</span>
                              <span>{source.section}</span>
                            </>
                          )}
                        </div>
                      </div>

                      {/* 高亮内容区域 */}
                      <div className="relative bg-yellow-50 border-l-4 border-yellow-500 rounded-lg p-6 shadow-sm">
                        {/* 脉冲动画点 */}
                        <div className="absolute -left-2 top-6 w-4 h-4 bg-yellow-500 rounded-full animate-pulse shadow-lg" />

                        {/* 高亮标签 */}
                        <div className="flex items-center gap-2 mb-3">
                          <div className="bg-yellow-500 text-white text-xs font-bold px-2 py-1 rounded">
                            高亮引用
                          </div>
                          <span className="text-xs text-yellow-800">AI 自动定位的关键内容</span>
                        </div>

                        {/* 高亮文本 */}
                        <div className="text-gray-900 leading-relaxed text-base">
                          {source.highlightText || source.snippet}
                        </div>
                      </div>

                      {/* 完整文档内容 */}
                      <div className="space-y-3 text-gray-700 leading-relaxed">
                        <h3 className="text-lg font-semibold text-gray-900 mb-3">完整段落内容</h3>
                        <p className="text-sm">{source.snippet}</p>
                      </div>

                      {/* 底部提示 */}
                      <div className="mt-6 pt-4 border-t border-gray-200">
                        <p className="text-xs text-gray-500 text-center">
                          提示：当前为文本预览模式。完整 PDF 查看需要后端服务支持。
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-between px-6 py-4 border-t border-gray-700 bg-gray-800/50">
              <Button
                variant="ghost"
                className="text-gray-400 hover:text-white hover:bg-gray-700"
                onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                disabled={!canPrev}
              >
                <ChevronLeft className="w-4 h-4 mr-2" />
                上一页
              </Button>
              <span className="text-sm text-gray-400">
                第 {currentPage} 页{numPages ? ` / 共 ${numPages} 页` : ""}
              </span>
              <Button
                variant="ghost"
                className="text-gray-400 hover:text-white hover:bg-gray-700"
                onClick={() => setCurrentPage((prev) => (numPages ? Math.min(numPages, prev + 1) : prev + 1))}
                disabled={!canNext}
              >
                下一页
                <ChevronRight className="w-4 h-4 ml-2" />
              </Button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
