"use client"

import { useEffect, useRef, useState, useMemo } from "react"
import { createPortal } from "react-dom"
import * as d3 from "d3"
import { motion, AnimatePresence } from "framer-motion"
import { Maximize2, X } from "lucide-react"
import { Button } from "@/components/ui/button"

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

export function KnowledgeGraphD3({ data, width = 600, height = 400, isAnimating = false }: KnowledgeGraphD3Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const fullscreenSvgRef = useRef<SVGSVGElement>(null)
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const selectedNodeIdRef = useRef<string | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [isMounted, setIsMounted] = useState(false)

  // 计算节点类型统计 - 包含所有可能的类型
  const nodeTypeStats = useMemo(() => {
    // 定义所有可能的节点类型
    const allTypes = [
      { type: "Hospital", label: "医院", color: "#8B7355" },
      { type: "DepartmentGroup", label: "部门", color: "#5B7FA8" },
      { type: "FunctionalZone", label: "功能分区", color: "#7B68A8" },
      { type: "Space", label: "空间", color: "#5A9B7D" },
      { type: "DesignMethod", label: "设计方法", color: "#C17A4F" },
      { type: "DesignMethodCategory", label: "设计方法分类", color: "#A89968" },
      { type: "Case", label: "案例", color: "#C97B9E" },
      { type: "Source", label: "资料来源", color: "#B85C6F" },
      { type: "KnowledgePoint", label: "知识点", color: "#5BA5A8" },
      { type: "MedicalService", label: "医疗服务", color: "#8B7BA8" },
      { type: "MedicalEquipment", label: "医疗设备", color: "#6B8BA8" },
      { type: "TreatmentMethod", label: "治疗方法", color: "#9B7BA8" },
    ]

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
  }, [data])

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

    const typeColors: Record<string, string> = {
      // 科技感配色 - 柔和不浓重的颜色
      Hospital: "#8B7355",              // 柔和金棕色 - 医院
      DepartmentGroup: "#5B7FA8",       // 柔和蓝色 - 部门
      FunctionalZone: "#7B68A8",        // 柔和紫色 - 功能分区
      Space: "#5A9B7D",                 // 柔和绿色 - 空间
      DesignMethod: "#C17A4F",          // 柔和橙色 - 设计方法
      DesignMethodCategory: "#A89968",  // 柔和金色 - 设计方法分类
      Case: "#C97B9E",                  // 柔和粉色 - 案例
      Source: "#B85C6F",                // 柔和红色 - 资料来源
      KnowledgePoint: "#5BA5A8",        // 柔和青色 - 知识点
      MedicalService: "#8B7BA8",        // 柔和蓝紫色 - 医疗服务
      MedicalEquipment: "#6B8BA8",      // 柔和钢蓝色 - 医疗设备
      TreatmentMethod: "#9B7BA8",       // 柔和兰花紫 - 治疗方法

      // 兼容旧的类型名称
      hospital: "#8B7355",
      room: "#5A9B7D",
      spec: "#7B68A8",
      document: "#B85C6F",

      // 默认
      entity: "#7A8A9A",
      concept: "#B8A858",
      relation: "#9B7BA8",
    }

    const getNodeColor = (type: string) => typeColors[type] || "#7A8A9A"

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
      .attr("fill", "#64748b")

    const link = container
      .append("g")
      .selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("stroke", "#64748b")
      .attr("stroke-width", isFullscreenMode ? 2 : 1.5)
      .attr("stroke-opacity", 0.7)
      .attr("marker-end", `url(#${isFullscreenMode ? "arrow-fullscreen" : "arrow"})`)

    const linkLabel = container
      .append("g")
      .selectAll("text")
      .data(links)
      .enter()
      .append("text")
      .attr("class", "link-label")
      .attr("font-size", isFullscreenMode ? "13px" : "11px")
      .attr("fill", "#cbd5e1")
      .attr("font-weight", "500")
      .attr("text-anchor", "middle")
      .attr("pointer-events", "none")
      .style("text-shadow", "0 0 3px rgba(0, 0, 0, 0.8), 0 0 6px rgba(0, 0, 0, 0.6)")
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
          }),
      )

    node
      .append("circle")
      .attr("r", nodeRadius)
      .attr("fill", (d) => {
        const color = getNodeColor(d.type)
        // 将颜色转换为半透明
        return color + "CC" // 添加CC的透明度 (约80%不透明度)
      })
      .attr("stroke", (d) => getNodeColor(d.type))
      .attr("stroke-width", isFullscreenMode ? 3 : 2)
      .style("filter", "drop-shadow(0 0 8px rgba(0,0,0,0.3))")
      .on("mouseenter", function (event, d) {
        setHoveredNode(d)
        d3.select(this)
          .transition()
          .duration(200)
          .attr("r", nodeRadius * 1.25)
          .attr("fill", (d: any) => getNodeColor(d.type) + "EE") // hover时增加不透明度
          .style("filter", "drop-shadow(0 0 12px currentColor)")
      })
      .on("mouseleave", function () {
        setHoveredNode(null)
        d3.select(this)
          .transition()
          .duration(200)
          .attr("r", nodeRadius)
          .attr("fill", (d: any) => getNodeColor(d.type) + "CC")
          .style("filter", "drop-shadow(0 0 8px rgba(0,0,0,0.3))")
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
        .attr("stroke-width", (d: any) => (nodeId && d.id === nodeId ? (isFullscreenMode ? 5 : 4) : (isFullscreenMode ? 3 : 2)))

      node.selectAll("text").attr("opacity", (d: any) => (nodeId ? (neighbors.has(d.id) ? 1 : 0.25) : 1))

      link
        .attr("stroke-opacity", (d: any) => {
          if (!nodeId) return 0.7
          const sid = d?.source?.id || d?.source
          const tid = d?.target?.id || d?.target
          return sid === nodeId || tid === nodeId ? 0.9 : 0.1
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
      .attr("fill", "#e2e8f0")
      .attr("font-weight", "500")
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

      link.attr("stroke-opacity", 0).transition().delay(800).duration(600).attr("stroke-opacity", 0.6)

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
          className="absolute top-2 right-2 bg-transparent hover:bg-white/10 text-white border-none p-1.5 h-auto w-auto"
          title="全屏查看"
        >
          <Maximize2 className="w-4 h-4" />
        </Button>

        {selectedNode && (
          <div className="absolute top-2 left-2 bg-black/80 backdrop-blur-sm px-3 py-2 rounded-lg border border-white/20">
            <p className="text-xs text-white">
              已选中: {selectedNode.label} <span className="text-gray-400">({selectedNode.type})</span>
            </p>
            <p className="text-[10px] text-gray-400">点击空白取消聚焦</p>
          </div>
        )}

        {hoveredNode && !selectedNode && (
          <div className="absolute top-2 left-2 bg-black/80 backdrop-blur-sm px-3 py-2 rounded-lg border border-white/20">
            <p className="text-xs text-white">
              节点: {hoveredNode.label} <span className="text-gray-400">({hoveredNode.type})</span>
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
                className="fixed inset-0 z-[9999] bg-black/98 backdrop-blur-xl flex items-center justify-center"
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
                    <h2 className="text-2xl font-semibold text-white flex items-center gap-3">
                      <span className="text-yellow-400">🔗</span>
                      知识图谱
                    </h2>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={handleCloseClick}
                      className="bg-white/10 hover:bg-white/20 text-white border border-white/20 rounded-lg"
                      title="关闭"
                    >
                      <X className="w-6 h-6" />
                    </Button>
                  </div>

                  {/* 节点类型统计图例 - 仅在全屏模式显示 */}
                  {nodeTypeStats.length > 0 && (
                    <div className="absolute top-24 left-8 z-10 bg-black/60 backdrop-blur-md rounded-lg border border-white/20 p-4 max-w-xs">
                      <h3 className="text-sm font-semibold text-white mb-3">节点类型统计</h3>
                      <div className="space-y-2">
                        {nodeTypeStats.map(stat => (
                          <div key={stat.type} className="flex items-center justify-between gap-4">
                            <div className="flex items-center gap-2">
                              <div
                                className="w-3 h-3 rounded-full border-2"
                                style={{
                                  backgroundColor: stat.color + "40",
                                  borderColor: stat.color
                                }}
                              />
                              <span className="text-xs text-gray-300">{stat.label}</span>
                            </div>
                            <span className="text-xs font-medium text-white">{stat.count}</span>
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
