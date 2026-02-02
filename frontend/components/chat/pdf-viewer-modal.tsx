"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { X, ChevronLeft, ChevronRight, ZoomIn, ZoomOut, PanelLeftClose, PanelLeftOpen, ArrowLeftRight, ArrowUpDown, RotateCcw } from 'lucide-react'
import { motion, AnimatePresence } from "framer-motion"
import { Document, Page, pdfjs } from "react-pdf"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { PDFSource } from "./pdf-source-card"
import { buildPageValueSummary } from "./pdf-citation-badge"

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
  const [isSidebarOpen, setIsSidebarOpen] = useState(true)
  const [fitMode, setFitMode] = useState<"free" | "width" | "page">("free")
  const [isInfoExpanded, setIsInfoExpanded] = useState(false)
  const viewerRef = useRef<HTMLDivElement>(null)
  const [viewerSize, setViewerSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 })

  // [FIX 2025-12-09] 修复 Hooks 顺序问题：将 useMemo 移到 early return 之前
  const highlightBoxes = useMemo(() => {
    if (!source || !source.positions || source.positions.length === 0) return []

    // [CHANGE 2025-12-29] 去除图片框选：只对文本引用做黄色高亮
    const contentType = source.contentType || 'text'
    const looksLikeImageCaption = String(source.highlightText || source.snippet || '').trim().startsWith('[图片')
    if (looksLikeImageCaption) return []
    if (contentType === 'image') return []

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
            contentType,  // [FIX 2025-12-27] 添加 content_type 用于差异化样式
          }
        }
        return null
      })
      .filter((box): box is { page: number; x0: number; y0: number; x1: number; y1: number; contentType: string } => Boolean(box))
  }, [source])

  useEffect(() => {
    if (source?.pageNumber) {
      setCurrentPage(source.pageNumber)
    }
  }, [source])

  useEffect(() => {
    // 重置 UI 状态（切换引用时保持一致体验）
    setFitMode("free")
    setScale(1.1)
    setIsInfoExpanded(false)
  }, [source?.id])

  useEffect(() => {
    const el = viewerRef.current
    if (!el || !isOpen) return

    const update = () => {
      const rect = el.getBoundingClientRect()
      setViewerSize({ width: Math.max(0, rect.width), height: Math.max(0, rect.height) })
    }

    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    window.addEventListener("resize", update)

    return () => {
      ro.disconnect()
      window.removeEventListener("resize", update)
    }
  }, [isOpen, isSidebarOpen])

  // [FIX 2025-12-09] 将 early return 移到所有 Hooks 之后
  if (!source) return null

  const currentPageHighlights = highlightBoxes.filter((box) => box.page === currentPage)

  const zoom = (direction: "in" | "out") => {
    setFitMode("free")
    setScale((prev) => {
      const next = direction === "in" ? prev + 0.1 : prev - 0.1
      return Math.min(Math.max(next, MIN_SCALE), MAX_SCALE)
    })
  }

  const canPrev = currentPage > 1
  const canNext = numPages ? currentPage < numPages : true

  const normalizeText = (t: string) => t.replace(/\s+/g, " ").trim()
  const sanitizeSideInfoText = (t: string) => {
    if (!t) return ""
    let s = t.replace(/\r\n?/g, "\n").replace(/\u00a0/g, " ")
    s = s.replace(/[★☆■◆●▶►▪▫]/g, "")
    s = s.replace(/\*\*/g, "").replace(/__/g, "").replace(/`{1,3}/g, "")
    s = s.replace(/^[ \t]*[—-]{2,}[ \t]*/g, "• ")
    s = s.replace(/[ \t]*[—-]{2,}[ \t]*/g, "\n• ")
    s = s
      .split("\n")
      .map((line) => line.replace(/[ \t]+/g, " ").trim())
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim()
    return s
  }
  const highlightText = normalizeText(source.highlightText || source.snippet || "")
  const snippetText = normalizeText(source.snippet || "")
  const showFullSnippet = Boolean(snippetText) && normalizeText(snippetText) !== normalizeText(highlightText)
  const valueSummary = buildPageValueSummary(source)
  const diagramType = inferDiagramType(`${source.title} ${source.section || ""} ${highlightText} ${snippetText}`)

  const sideInfoText = sanitizeSideInfoText(String(source.highlightText || source.snippet || ""))
  const infoPreview = sideInfoText.length > 220 ? `${sideInfoText.slice(0, 220)}…` : sideInfoText

  const jumpToPage = (page: number) => {
    if (!Number.isFinite(page)) return
    const clamped = Math.max(1, Math.min(numPages ?? page, page))
    setCurrentPage(clamped)
  }

  const handleFitWidth = () => setFitMode((m) => (m === "width" ? "free" : "width"))
  const handleFitPage = () => setFitMode((m) => (m === "page" ? "free" : "page"))

  const resetView = () => {
    setFitMode("free")
    setScale(1.1)
  }

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
            className="fixed inset-4 md:inset-8 z-[101] bg-gray-950 rounded-xl border border-white/10 shadow-2xl overflow-hidden"
          >
            <div className="flex h-full">
              {/* Sidebar */}
              <div
                className={cn(
                  "h-full shrink-0 border-r border-white/10 bg-black/30 backdrop-blur-md",
                  isSidebarOpen ? "w-[320px]" : "w-14",
                )}
              >
                <div className={cn("flex h-full flex-col overflow-y-auto", isSidebarOpen ? "p-4" : "p-2")}>
                  <div className={cn("flex items-start gap-2", isSidebarOpen ? "justify-between" : "flex-col items-center")}>
                    {isSidebarOpen ? (
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-white line-clamp-2" title={source.title}>
                          {source.title}
                        </div>
                        <div className="mt-1 text-[11px] text-gray-400">
                          第 {currentPage} 页{numPages ? ` / 共 ${numPages} 页` : ""}{source.section ? ` · ${source.section}` : ""}
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {diagramType && (
                            <span className="inline-flex items-center rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-gray-200">
                              {diagramType}
                            </span>
                          )}
                          {source.contentType && (
                            <span className="inline-flex items-center rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-gray-300">
                              {source.contentType === "image" ? "图像" : source.contentType === "table" ? "表格" : "文本"}
                            </span>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="text-[10px] text-gray-300 text-center leading-tight">
                        P{currentPage}
                      </div>
                    )}

                    <div className={cn("flex items-center gap-1", isSidebarOpen ? "" : "flex-col")}>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-gray-300 hover:text-white hover:bg-white/10"
                        onClick={() => setIsSidebarOpen((v) => !v)}
                        title={isSidebarOpen ? "收起侧边栏" : "展开侧边栏"}
                      >
                        {isSidebarOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeftOpen className="w-4 h-4" />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={onClose}
                        className="text-gray-300 hover:text-white hover:bg-white/10"
                        title="关闭"
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>

                  {/* Info */}
                  {isSidebarOpen && (
                    <div className="mt-4 space-y-3">
                      {valueSummary && (
                        <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] font-semibold text-gray-200">本页价值</div>
                          <div className="mt-1 text-[12px] text-gray-100 leading-relaxed">{valueSummary}</div>
                        </div>
                      )}

                      {sideInfoText && (
                        <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                          <div className="flex items-center justify-between gap-2">
                            <div className="text-[11px] font-semibold text-gray-200">重点说明</div>
                            {sideInfoText.length > 220 && (
                              <button
                                type="button"
                                className="text-[11px] text-blue-300 hover:text-blue-200"
                                onClick={() => setIsInfoExpanded((v) => !v)}
                              >
                                {isInfoExpanded ? "收起" : "展开"}
                              </button>
                            )}
                          </div>
                          <div className="mt-1 text-[12px] text-gray-100 leading-relaxed whitespace-pre-wrap">
                            {isInfoExpanded ? sideInfoText : infoPreview}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Controls */}
                  <div className={cn("mt-auto", isSidebarOpen ? "pt-4" : "pt-2")}>
                    <div className={cn("space-y-2", isSidebarOpen ? "" : "flex flex-col items-center gap-2 space-y-0")}>
                       {/* Page controls */}
                       <div className={cn("rounded-lg border border-white/10 bg-white/5", isSidebarOpen ? "p-3" : "p-1")}>
                         {isSidebarOpen && <div className="text-[11px] font-semibold text-gray-200 mb-2">文档控制</div>}
                        <div className={cn("grid gap-2", isSidebarOpen ? "grid-cols-2" : "grid-cols-1")}>
                          <Button
                            variant="ghost"
                            className={cn(
                              "text-gray-200 hover:text-white hover:bg-white/10",
                              isSidebarOpen ? "w-full justify-center" : "h-10 w-10 p-0 justify-center",
                            )}
                            onClick={() => jumpToPage(currentPage - 1)}
                            disabled={!canPrev}
                            title="上一页"
                          >
                            <ChevronLeft className={cn("w-4 h-4", isSidebarOpen ? "mr-2" : "")} />
                            {isSidebarOpen && "上一页"}
                          </Button>
                          <Button
                            variant="ghost"
                            className={cn(
                              "text-gray-200 hover:text-white hover:bg-white/10",
                              isSidebarOpen ? "w-full justify-center" : "h-10 w-10 p-0 justify-center",
                            )}
                            onClick={() => jumpToPage(currentPage + 1)}
                            disabled={!canNext}
                            title="下一页"
                          >
                            {isSidebarOpen && "下一页"}
                            <ChevronRight className={cn("w-4 h-4", isSidebarOpen ? "ml-2" : "")} />
                          </Button>
                        </div>
                      </div>

                      {/* View controls */}
                      <div className={cn("rounded-lg border border-white/10 bg-white/5", isSidebarOpen ? "p-3" : "p-1")}>
                        {isSidebarOpen && <div className="text-[11px] font-semibold text-gray-200 mb-2">视图控制</div>}
                        <div className={cn("grid gap-2", isSidebarOpen ? "grid-cols-2" : "grid-cols-1")}>
                          <Button
                            variant="ghost"
                            className={cn(
                              "text-gray-200 hover:text-white hover:bg-white/10",
                              isSidebarOpen ? "justify-start" : "h-10 w-10 p-0 justify-center",
                            )}
                            onClick={() => zoom("in")}
                            disabled={fitMode !== "free" ? false : scale >= MAX_SCALE}
                            title="放大"
                          >
                            <ZoomIn className={cn("w-4 h-4", isSidebarOpen ? "mr-2" : "")} />
                            {isSidebarOpen && "放大"}
                          </Button>
                          <Button
                            variant="ghost"
                            className={cn(
                              "text-gray-200 hover:text-white hover:bg-white/10",
                              isSidebarOpen ? "justify-start" : "h-10 w-10 p-0 justify-center",
                            )}
                            onClick={() => zoom("out")}
                            disabled={fitMode !== "free" ? false : scale <= MIN_SCALE}
                            title="缩小"
                          >
                            <ZoomOut className={cn("w-4 h-4", isSidebarOpen ? "mr-2" : "")} />
                            {isSidebarOpen && "缩小"}
                          </Button>
                          <Button
                            variant="ghost"
                            className={cn(
                              "text-gray-200 hover:text-white hover:bg-white/10",
                              isSidebarOpen ? "justify-start" : "h-10 w-10 p-0 justify-center",
                              fitMode === "width" && "bg-white/10",
                            )}
                            onClick={handleFitWidth}
                            title="适应宽度"
                          >
                            <ArrowLeftRight className={cn("w-4 h-4", isSidebarOpen ? "mr-2" : "")} />
                            {isSidebarOpen && "适应宽度"}
                          </Button>
                          <Button
                            variant="ghost"
                            className={cn(
                              "text-gray-200 hover:text-white hover:bg-white/10",
                              isSidebarOpen ? "justify-start" : "h-10 w-10 p-0 justify-center",
                              fitMode === "page" && "bg-white/10",
                            )}
                            onClick={handleFitPage}
                            title="适应页面"
                          >
                            <ArrowUpDown className={cn("w-4 h-4", isSidebarOpen ? "mr-2" : "")} />
                            {isSidebarOpen && "适应页面"}
                          </Button>

                          {isSidebarOpen && (
                            <Button
                              variant="ghost"
                              className="col-span-2 justify-start text-gray-200 hover:text-white hover:bg-white/10"
                              onClick={resetView}
                            >
                              <RotateCcw className="w-4 h-4 mr-2" />
                              重置视图
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Main viewer */}
              <div ref={viewerRef} className="flex-1 min-w-0 bg-gray-900/30">
                <div className="h-full overflow-auto">
                  {source.pdfUrl ? (
                    <div className="min-h-full w-full flex justify-center p-2 md:p-4">
                      <div className="relative bg-white/95 rounded-lg shadow-xl overflow-hidden">
                        <Document
                          file={source.pdfUrl}
                          loading={<div className="p-6 text-gray-200">正在加载 PDF ...</div>}
                          error={
                            <div className="p-6 bg-white min-h-[480px] w-full max-w-3xl">
                              <div className="space-y-4">
                                <div className="text-sm text-gray-600">
                                  PDF 文件加载失败，以下为文本预览（引用定位信息仍可用于设计决策）。
                                </div>
                                <div className="border border-gray-200 rounded-lg p-4 bg-yellow-50/60">
                                  <div className="text-xs font-semibold text-gray-700 mb-2">关键内容</div>
                                  <div className="text-sm text-gray-900 leading-relaxed">{highlightText}</div>
                                </div>
                                {showFullSnippet && (
                                  <div className="border border-gray-200 rounded-lg p-4">
                                    <div className="text-xs font-semibold text-gray-700 mb-2">上下文段落</div>
                                    <div className="text-sm text-gray-800 leading-relaxed">{snippetText}</div>
                                  </div>
                                )}
                              </div>
                            </div>
                          }
                          onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                        >
                          <div className="relative">
                            <Page
                              pageNumber={currentPage}
                              {...(fitMode === "width"
                                ? { width: Math.max(320, Math.floor(viewerSize.width - 24)) }
                                : fitMode === "page"
                                  ? { height: Math.max(320, Math.floor(viewerSize.height - 24)) }
                                  : { scale })}
                              renderAnnotationLayer={false}
                              renderTextLayer={false}
                            />
                            {currentPageHighlights.map((box, idx) => {
                              const highlightClass = "absolute border-2 border-yellow-400/80 bg-yellow-300/30 rounded-sm pointer-events-none"
                              return (
                                <div
                                  key={idx}
                                  className={highlightClass}
                                  style={{
                                    left: `${box.x0 * 100}%`,
                                    top: `${box.y0 * 100}%`,
                                    width: `${(box.x1 - box.x0) * 100}%`,
                                    height: `${(box.y1 - box.y0) * 100}%`,
                                  }}
                                />
                              )
                            })}
                          </div>
                        </Document>
                      </div>
                    </div>
                  ) : (
                    <div className="min-h-full w-full flex justify-center p-2 md:p-6">
                      <div className="w-full max-w-3xl bg-white rounded-xl shadow-2xl p-6">
                        <div className="space-y-4">
                          <div className="border-b border-gray-200 pb-3">
                            <h2 className="text-xl font-bold text-gray-900">{source.title}</h2>
                            <div className="mt-1 text-sm text-gray-600">
                              第 {source.pageNumber} 页{source.section ? ` · ${source.section}` : ""}
                            </div>
                          </div>
                          <div className="border border-yellow-200 bg-yellow-50 rounded-lg p-4">
                            <div className="text-xs font-semibold text-gray-700 mb-2">关键内容</div>
                            <div className="text-sm text-gray-900 leading-relaxed">{highlightText}</div>
                          </div>
                          {showFullSnippet && (
                            <div className="border border-gray-200 rounded-lg p-4">
                              <div className="text-xs font-semibold text-gray-700 mb-2">上下文段落</div>
                              <div className="text-sm text-gray-800 leading-relaxed">{snippetText}</div>
                            </div>
                          )}
                          <div className="text-xs text-gray-500">
                            提示：当前为文本预览模式；完整 PDF 预览需要后端可访问的 `pdfUrl`。
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}

function inferDiagramType(text: string): string | null {
  const t = String(text || "").replace(/\s+/g, " ").trim()
  if (!t) return null

  if (/(总平面|总图)/.test(t)) return "总平面"
  if (/(平面|布局|布置)/.test(t)) return "平面布置"
  if (/(剖面)/.test(t)) return "剖面"
  if (/(立面)/.test(t)) return "立面"
  if (/(节点|大样|构造)/.test(t)) return "节点详图"
  if (/(设备|安装|接口|管线)/.test(t)) return "设备/接口"

  return null
}
