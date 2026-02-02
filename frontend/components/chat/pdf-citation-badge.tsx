"use client"

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
  return (
    <button
      type="button"
      className="absolute pointer-events-auto rounded-md border border-blue-400/30 bg-blue-500/10 px-2 py-0.5 text-[11px] font-semibold text-blue-200 shadow-sm hover:bg-blue-500/20 transition-colors"
      style={style}
      onClick={onClick}
      title={`${source.title} · 第 ${source.pageNumber} 页`}
    >
      {citationNumber}
    </button>
  )
}

export function buildPageValueSummary(source: PDFSource): string | null {
  const title = String(source.title || "")
  const section = String(source.section || "")
  const highlight = String(source.highlightText || "")
  const snippet = String(source.snippet || "")
  const contentType = source.contentType || "text"

  const text = `${title} ${section} ${highlight} ${snippet}`

  if (contentType === "image") {
    if (/(平面|布局|布置)/.test(text) && /(详图|节点|构造|大样|设备)/.test(text)) {
      return "包含平面布置与关键节点/设备示意，可用于从整体到细部校对配置"
    }
    if (/(平面|布局|布置)/.test(text)) {
      return "展示空间平面布局与流线关系，适合快速建立功能分区与尺度感"
    }
    if (/(详图|节点|构造|大样|设备)/.test(text)) {
      return "提供节点/设备安装示意，适合深化设计与施工落地核对"
    }
    return "包含关键配图，可用于核对空间布置与设备点位关系"
  }

  if (/(规范|标准|要求|应当|必须|不得)/.test(text)) {
    return "汇总关键条文/指标，适合做合规性对照与参数校核"
  }

  if (/(流程|流线|洁污|人流|物流|无菌|污染|感控)/.test(text)) {
    return "聚焦功能流程/感染控制要点，适合用于流线与分区策略推敲"
  }

  if (/(平面|布局|布置|尺度|面积)/.test(text)) {
    return "提炼空间配置与尺度要点，适合用于方案阶段快速对照"
  }

  return null
}
