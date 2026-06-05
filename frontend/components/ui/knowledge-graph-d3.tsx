"use client"

import { useEffect, useRef, useState, useMemo } from "react"
import { createPortal } from "react-dom"
import * as d3 from "d3"
import { motion, AnimatePresence } from "framer-motion"
import { Maximize2, Network, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useT } from "@/lib/i18n"
import { getKnowledgeGraphNodeTypeItems } from "@/lib/i18n/ui-copy"

export interface GraphNode {
  id: string
  label: string
  type: string
  x?: number
  y?: number
  vx?: number
  vy?: number
  fx?: number | null
  fy?: number | null
}

export interface GraphLink {
  source: string | GraphNode
  target: string | GraphNode
  label: string
  isSynthetic?: boolean
  isVisualBridge?: boolean
  properties?: Record<string, unknown>
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

interface KnowledgeGraphD3Props {
  data: GraphData
  width?: number
  height?: number
  isAnimating?: boolean
}

const lightGraphTheme = {
  marker: "#6f8d96",
  link: "#7f9aa3",
  visualBridgeLink: "#a6b8bf",
  linkLabel: "#335158",
  linkLabelShadow: "0 1px 0 rgba(255,255,255,0.96), 0 0 5px rgba(255,255,255,0.9)",
  nodeLabel: "#12323a",
  selectedStroke: "#0e7490",
  nodeShadow: "drop-shadow(0 8px 14px rgba(15,78,99,0.18))",
  nodeHoverShadow: "drop-shadow(0 12px 20px rgba(14,116,144,0.24))",
} as const

const nodeTypePaints: Record<string, { fill: string; hoverFill: string; stroke: string }> = {
  Hospital: { fill: "#e0f2fe", hoverFill: "#bae6fd", stroke: "#0369a1" },
  DepartmentGroup: { fill: "#dcfce7", hoverFill: "#bbf7d0", stroke: "#059669" },
  FunctionalZone: { fill: "#ccfbf1", hoverFill: "#99f6e4", stroke: "#0f766e" },
  Space: { fill: "#ecfeff", hoverFill: "#cffafe", stroke: "#0891b2" },
  DesignMethod: { fill: "#eef2ff", hoverFill: "#e0e7ff", stroke: "#4f46e5" },
  DesignMethodCategory: { fill: "#f0fdfa", hoverFill: "#ccfbf1", stroke: "#0d9488" },
  Case: { fill: "#ffe4e6", hoverFill: "#fecdd3", stroke: "#be123c" },
  Source: { fill: "#f1f5f9", hoverFill: "#e2e8f0", stroke: "#475569" },
  KnowledgePoint: { fill: "#f7fee7", hoverFill: "#ecfccb", stroke: "#65a30d" },
  MedicalService: { fill: "#e0f2fe", hoverFill: "#bae6fd", stroke: "#0284c7" },
  MedicalEquipment: { fill: "#dbeafe", hoverFill: "#bfdbfe", stroke: "#2563eb" },
  TreatmentMethod: { fill: "#fce7f3", hoverFill: "#fbcfe8", stroke: "#db2777" },
  hospital: { fill: "#e0f2fe", hoverFill: "#bae6fd", stroke: "#0369a1" },
  room: { fill: "#ecfeff", hoverFill: "#cffafe", stroke: "#0891b2" },
  spec: { fill: "#eef2ff", hoverFill: "#e0e7ff", stroke: "#4f46e5" },
  document: { fill: "#f1f5f9", hoverFill: "#e2e8f0", stroke: "#475569" },
  entity: { fill: "#f8fafc", hoverFill: "#f1f5f9", stroke: "#64748b" },
  concept: { fill: "#f7fee7", hoverFill: "#ecfccb", stroke: "#65a30d" },
  relation: { fill: "#f0fdfa", hoverFill: "#ccfbf1", stroke: "#0d9488" },
}

function getNodePaint(type: string) {
  return nodeTypePaints[type] || nodeTypePaints.entity
}

export function KnowledgeGraphD3({ data, width = 600, height = 400, isAnimating = false }: KnowledgeGraphD3Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const fullscreenSvgRef = useRef<SVGSVGElement>(null)
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const selectedNodeIdRef = useRef<string | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [isMounted, setIsMounted] = useState(false)
  const { t } = useT()

  // 计算节点类型统计 - 包含所有可能的类型
  const nodeTypeStats = useMemo(() => {
    const allTypes = getKnowledgeGraphNodeTypeItems(t)

    // 统计当前图谱中的节点数量
    const counts = new Map<string, number>()
    data.nodes.forEach(node => {
      counts.set(node.type, (counts.get(node.type) || 0) + 1)
    })

    // 返回所有类型及其数量（不存在的为0）
    return allTypes.map(typeInfo => ({
      ...typeInfo,
      count: counts.get(typeInfo.type) || 0
    }))
  }, [data, t])
  const nodeTypeLabelMap = useMemo(
    () => Object.fromEntries(nodeTypeStats.map((item) => [item.type, item.label])),
    [nodeTypeStats],
  )

  useEffect(() => {
    setIsMounted(true)
    return () => setIsMounted(false)
  }, [])

  useEffect(() => {
    setHoveredNode(null)
    setSelectedNode(null)
    selectedNodeIdRef.current = null
  }, [data])

  const renderGraph = (svgElement: SVGSVGElement, w: number, h: number, isFullscreenMode = false) => {
    const svg = d3.select(svgElement)
    svg.selectAll("*").remove()

    const actualWidth = svgElement.clientWidth || w
    const actualHeight = svgElement.clientHeight || h

    const nodeRadius = isFullscreenMode ? 35 : 20
    const linkDistance = isFullscreenMode ? 250 : 150
    const chargeStrength = isFullscreenMode ? -800 : -500
    const fontSize = isFullscreenMode ? "14px" : "11px"
    const labelOffset = isFullscreenMode ? 50 : 35

    const centerX = actualWidth / 2
    const centerY = actualHeight / 2
    const nodesCount = Math.max(1, data.nodes.length)
    const initialSpread = Math.min(actualWidth, actualHeight) * 0.02

    // Clone nodes so we don't mutate incoming props and start everyone from the container center
    const nodes = data.nodes.map((node, index) => {
      const angle = (index / nodesCount) * Math.PI * 2
      const radius = initialSpread * (isFullscreenMode ? 1.5 : 1)
      return {
        ...node,
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        fx: null,
        fy: null,
      }
    })

    // Clone links so D3 forceLink doesn't mutate React state (it rewrites source/target into node objects)
    // Also normalize potentially-mutated source/target back to ids (string) so re-renders stay stable.
    const links = data.links.map((link) => ({
      ...link,
      source: typeof link.source === "string" ? link.source : link.source.id,
      target: typeof link.target === "string" ? link.target : link.target.id,
    }))

    const container = svg.append("g")

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.5, 3])
      .on("zoom", (event) => {
        container.attr("transform", event.transform)
      })

    svg.call(zoom)

    const simulation = d3
      .forceSimulation(nodes as d3.SimulationNodeDatum[])
      .force(
        "link",
        d3
          .forceLink(links)
          .id((d: any) => d.id)
          .distance(linkDistance),
      )
      .force("charge", d3.forceManyBody().strength(chargeStrength))
      .force("center", d3.forceCenter(actualWidth / 2, actualHeight / 2))
      .force("collision", d3.forceCollide().radius(nodeRadius * 1.5))
      .force("x", d3.forceX(actualWidth / 2).strength(0.05))
      .force("y", d3.forceY(actualHeight / 2).strength(0.05))

    const getBaseLinkOpacity = (linkData: GraphLink) => (linkData.isVisualBridge ? 0.38 : 0.58)
    const getSelectedLinkOpacity = (linkData: GraphLink) => (linkData.isVisualBridge ? 0.62 : 0.82)

    svg
      .append("defs")
      .selectAll("marker")
      .data(["arrow"])
      .enter()
      .append("marker")
      .attr("id", isFullscreenMode ? "arrow-fullscreen" : "arrow")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", nodeRadius + 5)
      .attr("refY", 0)
      .attr("markerWidth", isFullscreenMode ? 5 : 4)
      .attr("markerHeight", isFullscreenMode ? 5 : 4)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", lightGraphTheme.marker)

    const link = container
      .append("g")
      .selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("stroke", (d: any) => (d.isVisualBridge ? lightGraphTheme.visualBridgeLink : lightGraphTheme.link))
      .attr("stroke-width", (d: any) => (d.isVisualBridge ? (isFullscreenMode ? 1.5 : 1.2) : (isFullscreenMode ? 2 : 1.5)))
      .attr("stroke-opacity", (d: any) => getBaseLinkOpacity(d))
      .attr("stroke-dasharray", (d: any) => (d.isVisualBridge ? (isFullscreenMode ? "10 6" : "7 5") : null))
      .attr("marker-end", `url(#${isFullscreenMode ? "arrow-fullscreen" : "arrow"})`)

    const linkLabel = container
      .append("g")
      .selectAll("text")
      .data(links)
      .enter()
      .append("text")
      .attr("class", "link-label")
      .attr("font-size", isFullscreenMode ? "13px" : "11px")
      .attr("fill", lightGraphTheme.linkLabel)
      .attr("font-weight", "500")
      .attr("text-anchor", "middle")
      .attr("pointer-events", "none")
      .attr("opacity", (d: any) => (d.isVisualBridge ? 0.55 : 1))
      .style("text-shadow", lightGraphTheme.linkLabelShadow)
      .text((d) => d.label)

    const node = container
      .append("g")
      .selectAll("g")
      .data(nodes)
      .enter()
      .append("g")
      .attr("class", "node")
      .style("cursor", "pointer")
      .call(
        d3
          .drag<SVGGElement, GraphNode>()
          .on("start", (event, d: any) => {
            if (!event.active) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          })
          .on("drag", (event, d: any) => {
            d.fx = event.x
            d.fy = event.y
          })
          .on("end", (event, d: any) => {
            if (!event.active) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          }) as any,
      )

    node
      .append("circle")
      .attr("r", nodeRadius)
      .attr("fill", (d) => getNodePaint(d.type).fill)
      .attr("stroke", (d) => getNodePaint(d.type).stroke)
      .attr("stroke-width", isFullscreenMode ? 3 : 2)
      .style("filter", lightGraphTheme.nodeShadow)
      .on("mouseenter", function (event, d) {
        setHoveredNode(d)
        d3.select(this)
          .transition()
          .duration(200)
          .attr("r", nodeRadius * 1.25)
          .attr("fill", (d: any) => getNodePaint(d.type).hoverFill)
          .style("filter", lightGraphTheme.nodeHoverShadow)
      })
      .on("mouseleave", function () {
        setHoveredNode(null)
        d3.select(this)
          .transition()
          .duration(200)
          .attr("r", nodeRadius)
          .attr("fill", (d: any) => getNodePaint(d.type).fill)
          .style("filter", lightGraphTheme.nodeShadow)
      })

    // Build adjacency map for click-to-focus interaction
    const neighborMap = new Map<string, Set<string>>()
    for (const l of links as any[]) {
      const s = typeof l.source === "string" ? l.source : l.source.id
      const t = typeof l.target === "string" ? l.target : l.target.id
      if (!s || !t) continue
      if (!neighborMap.has(s)) neighborMap.set(s, new Set())
      if (!neighborMap.has(t)) neighborMap.set(t, new Set())
      neighborMap.get(s)?.add(t)
      neighborMap.get(t)?.add(s)
    }

    const applySelection = (nodeId: string | null) => {
      selectedNodeIdRef.current = nodeId
      const neighbors = nodeId ? neighborMap.get(nodeId) || new Set<string>() : new Set<string>()
      if (nodeId) neighbors.add(nodeId)

      node
        .selectAll("circle")
        .attr("opacity", (d: any) => (nodeId ? (neighbors.has(d.id) ? 1 : 0.15) : 1))
        .attr("stroke", (d: any) => (nodeId && d.id === nodeId ? lightGraphTheme.selectedStroke : getNodePaint(d.type).stroke))
        .attr("stroke-width", (d: any) => (nodeId && d.id === nodeId ? (isFullscreenMode ? 5 : 4) : (isFullscreenMode ? 3 : 2)))

      node.selectAll("text").attr("opacity", (d: any) => (nodeId ? (neighbors.has(d.id) ? 1 : 0.25) : 1))

      link
        .attr("stroke-opacity", (d: any) => {
          if (!nodeId) return getBaseLinkOpacity(d)
          const sid = d?.source?.id || d?.source
          const tid = d?.target?.id || d?.target
          if (sid === nodeId || tid === nodeId) {
            return getSelectedLinkOpacity(d)
          }
          return 0.1
        })
        .attr("marker-end", (d: any) => {
          if (!nodeId) return `url(#${isFullscreenMode ? "arrow-fullscreen" : "arrow"})`
          const sid = d?.source?.id || d?.source
          const tid = d?.target?.id || d?.target
          return sid === nodeId || tid === nodeId ? `url(#${isFullscreenMode ? "arrow-fullscreen" : "arrow"})` : "none"
        })

      linkLabel.attr("opacity", (d: any) => {
        if (!nodeId) return 1
        const sid = d?.source?.id || d?.source
        const tid = d?.target?.id || d?.target
        return sid === nodeId || tid === nodeId ? 1 : 0.1
      })
    }

    node.on("click", function (event: any, d: any) {
      event.stopPropagation()
      if (event.defaultPrevented) return
      const current = selectedNodeIdRef.current
      const next = current === d.id ? null : d.id
      applySelection(next)
      setSelectedNode(next ? d : null)
    })

    svg.on("click", function (event: any) {
      if (event.defaultPrevented) return
      applySelection(null)
      setSelectedNode(null)
    })

    node
      .append("text")
      .attr("text-anchor", "middle")
      .attr("dy", labelOffset)
      .attr("font-size", fontSize)
      .attr("fill", lightGraphTheme.nodeLabel)
      .attr("font-weight", "500")
      .style("text-shadow", lightGraphTheme.linkLabelShadow)
      .text((d) => d.label)

    if (isAnimating) {
      node
        .selectAll("circle")
        .attr("r", 0)
        .style("opacity", 0)
        .transition()
        .duration(600)
        .delay((d, i) => i * 100)
        .attr("r", nodeRadius)
        .style("opacity", 1)
        .ease(d3.easeElasticOut.amplitude(1).period(0.5))

      node
        .selectAll("text")
        .style("opacity", 0)
        .transition()
        .duration(400)
        .delay((d, i) => i * 100 + 300)
        .style("opacity", 1)

      link.attr("stroke-opacity", 0).transition().delay(800).duration(600).attr("stroke-opacity", (d: any) => getBaseLinkOpacity(d))

      linkLabel.style("opacity", 0).transition().delay(1000).duration(400).style("opacity", 1)
    }

    simulation.on("tick", () => {
      link
        .attr("x1", (d: any) => d.source.x)
        .attr("y1", (d: any) => d.source.y)
        .attr("x2", (d: any) => d.target.x)
        .attr("y2", (d: any) => d.target.y)

      linkLabel
        .attr("x", (d: any) => (d.source.x + d.target.x) / 2)
        .attr("y", (d: any) => (d.source.y + d.target.y) / 2)

      node.attr("transform", (d: any) => `translate(${d.x},${d.y})`)
    })

    return () => {
      simulation.stop()
    }
  }

  useEffect(() => {
    if (!svgRef.current || !data.nodes.length || isFullscreen) return
    return renderGraph(svgRef.current, width, height, false)
  }, [data, width, height, isAnimating, isFullscreen])

  useEffect(() => {
    if (!fullscreenSvgRef.current || !data.nodes.length || !isFullscreen) return
    console.log("[v0] Rendering fullscreen graph")
    const fullscreenWidth = window.innerWidth - 64
    const fullscreenHeight = window.innerHeight - 64
    return renderGraph(fullscreenSvgRef.current, fullscreenWidth, fullscreenHeight, true)
  }, [data, isFullscreen])

  const handleExpandClick = () => {
    console.log("[v0] Expand button clicked, setting fullscreen to true")
    setIsFullscreen(true)
  }

  const handleCloseClick = () => {
    console.log("[v0] Close button clicked, setting fullscreen to false")
    setIsFullscreen(false)
  }

  return (
    <>
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="relative w-full h-full"
      >
        <svg
          ref={svgRef}
          width="100%"
          height="100%"
          className="w-full h-full"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMidYMid meet"
        />

        <Button
          variant="ghost"
          size="icon"
          onClick={handleExpandClick}
          className="absolute top-2 right-2 h-auto w-auto rounded-full border border-[#cfe2e7] bg-white/86 p-1.5 text-[#0f4e63] shadow-[0_8px_20px_rgba(15,78,99,0.12)] transition-colors hover:border-[#0e7490]/40 hover:bg-[#e6f4f6]"
          title={t('graph.expand')}
        >
          <Maximize2 className="w-4 h-4" />
        </Button>

        {selectedNode && (
          <div className="absolute top-2 left-2 rounded-lg border border-[#cfe2e7] bg-white/92 px-3 py-2 shadow-[0_10px_28px_rgba(15,78,99,0.12)] backdrop-blur-sm">
            <p className="text-xs text-[#12323a]">
              {t('graph.selected')}: {selectedNode.label} <span className="text-[#6c858c]">({nodeTypeLabelMap[selectedNode.type] || selectedNode.type})</span>
            </p>
            <p className="text-[10px] text-[#6c858c]">{t('graph.clearSelection')}</p>
          </div>
        )}

        {hoveredNode && !selectedNode && (
          <div className="absolute top-2 left-2 rounded-lg border border-[#cfe2e7] bg-white/92 px-3 py-2 shadow-[0_10px_28px_rgba(15,78,99,0.12)] backdrop-blur-sm">
            <p className="text-xs text-[#12323a]">
              {t('graph.node')}: {hoveredNode.label} <span className="text-[#6c858c]">({nodeTypeLabelMap[hoveredNode.type] || hoveredNode.type})</span>
            </p>
          </div>
        )}
      </motion.div>

      {isMounted &&
        createPortal(
          <AnimatePresence>
            {isFullscreen && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-[9999] bg-[#f7fbfc]/96 backdrop-blur-xl flex items-center justify-center"
                onClick={handleCloseClick}
              >
                <motion.div
                  initial={{ scale: 0.95, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={{ scale: 0.95, opacity: 0 }}
                  transition={{ type: "spring", damping: 25, stiffness: 300 }}
                  className="relative w-full h-full p-8"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="absolute top-8 left-8 right-8 z-10 flex items-center justify-between">
                    <h2 className="text-2xl font-semibold text-[#12323a] flex items-center gap-3">
                      <Network className="h-6 w-6 text-[#0e7490]" />
                      {t('graph.fullscreenTitle')}
                    </h2>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={handleCloseClick}
                      className="rounded-lg border border-[#cfe2e7] bg-white/86 text-[#0f4e63] shadow-[0_10px_28px_rgba(15,78,99,0.12)] transition-colors hover:border-[#0e7490]/40 hover:bg-[#e6f4f6]"
                      title={t('graph.close')}
                    >
                      <X className="w-6 h-6" />
                    </Button>
                  </div>

                  {/* 节点类型统计图例 - 仅在全屏模式显示 */}
                  {nodeTypeStats.length > 0 && (
                    <div className="absolute top-24 left-8 z-10 max-w-xs rounded-lg border border-[#cfe2e7] bg-white/88 p-4 shadow-[0_18px_46px_rgba(15,78,99,0.14)] backdrop-blur-md">
                      <h3 className="text-sm font-semibold text-[#12323a] mb-3">{t('graph.nodeTypeStats')}</h3>
                      <div className="space-y-2">
                        {nodeTypeStats.map(stat => (
                          <div key={stat.type} className="flex items-center justify-between gap-4">
                            <div className="flex items-center gap-2">
                              <div
                                className="w-3 h-3 rounded-full border-2"
                                style={{
                                  backgroundColor: getNodePaint(stat.type).fill,
                                  borderColor: getNodePaint(stat.type).stroke
                                }}
                              />
                              <span className="text-xs text-[#335158]">{stat.label}</span>
                            </div>
                            <span className="text-xs font-medium text-[#12323a]">{stat.count}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="w-full h-full pt-16">
                    <svg
                      ref={fullscreenSvgRef}
                      width="100%"
                      height="100%"
                      className="w-full h-full"
                      preserveAspectRatio="xMidYMid meet"
                    />
                  </div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}
    </>
  )
}
