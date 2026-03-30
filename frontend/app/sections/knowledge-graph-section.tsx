"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import * as d3 from "d3"
import { Maximize2, X, Share2, GitBranch, ChevronUp, ChevronDown } from "lucide-react"
import { useT } from "@/lib/i18n"

interface KnowledgeGraphSectionProps {
  onNavigate: (sectionIndex: number) => void
}

interface GraphNode {
  id: string
  cluster: string
  label: string
  value: number
}

interface GraphLink {
  source: string
  target: string
  strength: number
}

interface TreeNode {
  name: string
  children?: TreeNode[]
  size?: number
}

type ForceNode = GraphNode & d3.SimulationNodeDatum
type ForceLink = GraphLink & d3.SimulationLinkDatum<ForceNode>

type ViewMode = "force" | "radial"

const CLUSTER_LABELS = ["标准规范", "学术论文", "设计案例", "政策文件"]

export default function KnowledgeGraphSection({ onNavigate }: KnowledgeGraphSectionProps) {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [graphView, setGraphView] = useState<ViewMode>("force")
  const [isPreviewActive, setIsPreviewActive] = useState(false)
  const sectionRef = useRef<HTMLElement | null>(null)
  const { t } = useT()

  const previewSvgRef = useRef<SVGSVGElement | null>(null)
  const fullscreenSvgRef = useRef<SVGSVGElement | null>(null)

  const baseNodes = useMemo(() => createDemoNodes(), [])
  const baseLinks = useMemo(() => createDemoLinks(baseNodes), [baseNodes])
  const treeData = useMemo(() => createTreeData(baseNodes), [baseNodes])

  useEffect(() => {
    const element = sectionRef.current
    if (!element) return

    if (typeof IntersectionObserver === "undefined") {
      setIsPreviewActive(true)
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries
        setIsPreviewActive(Boolean(entry?.isIntersecting))
      },
      { root: null, rootMargin: "800px 0px", threshold: 0.1 },
    )

    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!isPreviewActive) return
    return renderForceGraph(previewSvgRef.current, baseNodes, baseLinks, { enableZoom: false })
  }, [baseLinks, baseNodes, isPreviewActive])

  useEffect(() => {
    if (!isFullscreen) return
    if (graphView === "force") {
      return renderForceGraph(fullscreenSvgRef.current, baseNodes, baseLinks, { enableZoom: true })
    }
    return renderRadialTree(fullscreenSvgRef.current, treeData, { enableZoom: true })
  }, [isFullscreen, graphView, baseNodes, baseLinks, treeData])

  useEffect(() => {
    if (!isFullscreen) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsFullscreen(false)
      }
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [isFullscreen])

  return (
    <section
      id="section-2"
      ref={sectionRef}
      className="relative min-h-screen flex flex-col justify-center overflow-hidden bg-black text-white"
    >
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,#1f2937,transparent_55%)]" />
      <div className="absolute inset-0 opacity-40 blur-3xl bg-gradient-to-br from-teal-500 via-cyan-500/40 to-indigo-500/20" />

      {/* 向上跳转按钮 */}
      <div className="absolute top-24 left-1/2 transform -translate-x-1/2 z-50">
        <button
          onClick={() => onNavigate(1)}
          className="text-white/60 hover:text-white transition-colors animate-bounce"
          aria-label={t('kg.aria.jumpKb')}
        >
          <ChevronUp className="w-6 h-6" />
        </button>
      </div>

      {/* 向下跳转按钮 */}
      <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 z-50">
        <button
          onClick={() => onNavigate(3)}
          className="text-white/60 hover:text-white transition-colors animate-bounce"
          aria-label={t('kg.aria.jumpNext')}
        >
          <ChevronDown className="w-6 h-6" />
        </button>
      </div>

      <div className="relative z-10 max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-[1.1fr_1.2fr] gap-10 px-6 py-16">
        <div className="space-y-6">
          <p className="text-sm text-cyan-200 tracking-[0.3em] uppercase">{t('kg.sectionLabel')}</p>
          <h2 className="text-4xl md:text-5xl font-semibold leading-tight bg-gradient-to-br from-white via-slate-200 to-slate-500 bg-clip-text text-transparent">
            {t('kg.title')}
          </h2>
          <p className="text-base text-white/80 leading-relaxed">
            {t('kg.desc')}
          </p>

          <div className="grid grid-cols-2 gap-6 pt-4">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-5 backdrop-blur">
              <p className="text-3xl font-bold">{t('kg.stat.nodes')}</p>
              <p className="text-sm text-white/70">{t('kg.stat.nodesLabel')}</p>
              <span className="text-xs text-cyan-200 mt-2 inline-flex">{t('kg.stat.nodesDetail')}</span>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-5 backdrop-blur">
              <p className="text-3xl font-bold">{t('kg.stat.links')}</p>
              <p className="text-sm text-white/70">{t('kg.stat.linksLabel')}</p>
              <span className="text-xs text-cyan-200 mt-2 inline-flex">{t('kg.stat.linksDetail')}</span>
            </div>
          </div>

          <div className="space-y-3 text-sm text-white/70">
            <p>{t('kg.feature.1')}</p>
            <p>{t('kg.feature.2')}</p>
            <p>{t('kg.feature.3')}</p>
          </div>

          <button
            onClick={() => onNavigate(1)}
            className="inline-flex items-center justify-center rounded-full border border-white/20 px-6 py-2 text-sm font-medium text-white transition hover:bg-white/10"
          >
            {t('kg.btn.jumpKb')}
          </button>
        </div>

        <div className="relative rounded-[32px] border border-white/10 bg-black/60 p-6 backdrop-blur-3xl">
          <div className="absolute inset-0 rounded-[32px] bg-gradient-to-br from-white/10 via-transparent to-transparent pointer-events-none" />
          <div className="relative z-10 flex items-center justify-between text-sm text-white/70 mb-4">
            <div className="uppercase tracking-[0.3em] text-xs text-cyan-200">{t('kg.preview')}</div>
            <button
              onClick={() => {
                setGraphView("force")
                setIsFullscreen(true)
              }}
              className="inline-flex items-center gap-2 rounded-full border border-white/20 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-white/10"
            >
              <Maximize2 className="h-3.5 w-3.5" />
              {t('kg.btn.fullscreen')}
            </button>
          </div>
          <svg ref={previewSvgRef} className="relative z-10 h-[420px] w-full" />
        </div>
      </div>
      {isFullscreen && (
        <div className="fixed inset-0 z-[200]">
          <div className="absolute inset-0 bg-black/80 backdrop-blur" onClick={() => setIsFullscreen(false)} />
          <div
            className="relative z-10 flex h-full flex-col"
            onClick={(event) => {
              event.stopPropagation()
            }}
          >
            <div className="flex flex-wrap items-center justify-between gap-4 px-8 pt-8 text-white">
              <div>
                <p className="text-sm uppercase tracking-[0.4em] text-cyan-200">Knowledge Graph</p>
                <h3 className="text-2xl font-semibold mt-2">{t('kg.fullscreenTitle')}</h3>
              </div>
              <div className="flex items-center gap-3">
                <div className="inline-flex rounded-full border border-white/20 p-1">
                  <button
                    onClick={() => setGraphView("force")}
                    className={`inline-flex items-center gap-1 rounded-full px-4 py-1.5 text-sm transition ${
                      graphView === "force" ? "bg-white text-black" : "text-white hover:bg-white/10"
                    }`}
                  >
                    <Share2 className="h-4 w-4" />
                    {t('kg.viewForce')}
                  </button>
                  <button
                    onClick={() => setGraphView("radial")}
                    className={`inline-flex items-center gap-1 rounded-full px-4 py-1.5 text-sm transition ${
                      graphView === "radial" ? "bg-white text-black" : "text-white hover:bg-white/10"
                    }`}
                  >
                    <GitBranch className="h-4 w-4" />
                    {t('kg.viewRadial')}
                  </button>
                </div>
                <button
                  onClick={() => setIsFullscreen(false)}
                  className="rounded-full border border-white/20 p-2 text-white transition hover:bg-white/10"
                  aria-label={t('kg.aria.closeFullscreen')}
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
            </div>
            <div className="flex-1 px-8 py-6">
              <div className="relative h-full w-full rounded-[32px] border border-white/20 bg-black/60 p-4">
                <svg ref={fullscreenSvgRef} className="h-full w-full" />
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function createDemoNodes(): GraphNode[] {
  return Array.from({ length: 40 }, (_, index) => {
    const cluster = CLUSTER_LABELS[index % CLUSTER_LABELS.length]
    return {
      id: `node-${index}`,
      cluster,
      label: `${cluster}-${(index % 10) + 1}`,
      value: 8 + Math.random() * 8,
    }
  })
}

function createDemoLinks(nodes: GraphNode[]): GraphLink[] {
  return Array.from({ length: 65 }, () => {
    const sourceIndex = Math.floor(Math.random() * nodes.length)
    let targetIndex = Math.floor(Math.random() * nodes.length)
    while (targetIndex === sourceIndex) {
      targetIndex = Math.floor(Math.random() * nodes.length)
    }
    return {
      source: nodes[sourceIndex].id,
      target: nodes[targetIndex].id,
      strength: Math.random() * 0.8 + 0.2,
    }
  })
}

function createTreeData(nodes: GraphNode[]): TreeNode {
  return {
    name: "MediArch",
    children: CLUSTER_LABELS.map((cluster) => ({
      name: cluster,
      children: nodes
        .filter((node) => node.cluster === cluster)
        .slice(0, 8)
        .map((node) => ({
          name: node.label,
          size: node.value,
        })),
    })),
  }
}

function renderForceGraph(
  svgElement: SVGSVGElement | null,
  nodesData: GraphNode[],
  linksData: GraphLink[],
  options: { enableZoom: boolean },
) {
  if (!svgElement) return

  const width = svgElement.clientWidth || 800
  const height = svgElement.clientHeight || 600

  const svg = d3.select(svgElement)
  svg.selectAll("*").remove()
  svg.attr("viewBox", `0 0 ${width} ${height}`)
  svg.attr("preserveAspectRatio", "xMidYMid meet")

  const container = svg.append("g")

  if (options.enableZoom) {
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.4, 3])
      .on("zoom", (event) => {
        container.attr("transform", event.transform)
      })
    svg.call(zoom as any)
  } else {
    svg.on(".zoom", null)
  }

  const nodes: ForceNode[] = nodesData.map((node) => ({ ...node }))
  const links: ForceLink[] = linksData.map((link) => ({ ...link }))

  const colorScale = d3.scaleOrdinal<string>().domain(CLUSTER_LABELS).range(["#2dd4bf", "#38bdf8", "#818cf8", "#f472b6"])

  const link = container
    .append("g")
    .attr("stroke", "rgba(255,255,255,0.25)")
    .attr("stroke-opacity", 0.6)
    .selectAll("line")
    .data(links)
    .join("line")
    .attr("stroke-width", (d) => 0.5 + (d.strength ?? 0.6) * 1.2)
    .attr("stroke-dasharray", (d) => ((d.strength ?? 0) > 0.8 ? "2 3" : ""))

  const node = container
    .append("g")
    .selectAll("circle")
    .data(nodes)
    .join("circle")
    .attr("r", (d) => d.value)
    .attr("fill", (d) => colorScale(d.cluster))
    .attr("stroke", "rgba(255,255,255,0.6)")
    .attr("stroke-width", 0.5)
    .call(
      d3
        .drag<SVGCircleElement, GraphNode & d3.SimulationNodeDatum>()
        .on("start", (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on("drag", (event, d) => {
          d.fx = event.x
          d.fy = event.y
        })
        .on("end", (event, d) => {
          if (!event.active) simulation.alphaTarget(0)
          d.fx = null
          d.fy = null
        }) as any,
    )

  const labels = container
    .append("g")
    .selectAll("text")
    .data(nodes)
    .join("text")
    .text((d) => d.label)
    .attr("font-size", 10)
    .attr("fill", "rgba(255,255,255,0.85)")
    .attr("text-anchor", "middle")
    .attr("pointer-events", "none")

  const simulation = d3
    .forceSimulation(nodes)
    .force(
      "link",
      d3
        .forceLink<ForceNode, ForceLink>(links)
        .id((d) => d.id)
        .distance((d) => 70 + (d.strength ?? 0.5) * 90)
        .strength((d) => d.strength ?? 0.6),
    )
    .force("charge", d3.forceManyBody().strength(options.enableZoom ? -220 : -120))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force(
      "collision",
      d3.forceCollide<GraphNode & d3.SimulationNodeDatum>().radius((d) => d.value + 10),
    )

  simulation.on("tick", () => {
    link
      .attr("x1", (d) => (((d.source as ForceNode).x as number) ?? 0))
      .attr("y1", (d) => (((d.source as ForceNode).y as number) ?? 0))
      .attr("x2", (d) => (((d.target as ForceNode).x as number) ?? 0))
      .attr("y2", (d) => (((d.target as ForceNode).y as number) ?? 0))

    node.attr("cx", (d) => d.x ?? 0).attr("cy", (d) => d.y ?? 0)
    labels.attr("x", (d) => d.x ?? 0).attr("y", (d) => (d.y ?? 0) - d.value - 4)
  })

  return () => {
    simulation.stop()
    svg.selectAll("*").remove()
    svg.on(".zoom", null)
  }
}

function renderRadialTree(svgElement: SVGSVGElement | null, treeData: TreeNode, options: { enableZoom: boolean }) {
  if (!svgElement) return

  const width = svgElement.clientWidth || 800
  const height = svgElement.clientHeight || 600
  const radius = Math.min(width, height) / 2 - 40

  const svg = d3.select(svgElement)
  svg.selectAll("*").remove()
  svg.attr("viewBox", `0 0 ${width} ${height}`)
  svg.attr("preserveAspectRatio", "xMidYMid meet")

  const zoomContainer = svg.append("g")

  if (options.enableZoom) {
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.4, 3])
      .on("zoom", (event) => {
        zoomContainer.attr("transform", event.transform)
      })
    svg.call(zoom as any)
    svg.call(zoom.transform, d3.zoomIdentity.translate(width / 2, height / 2))
  } else {
    zoomContainer.attr("transform", `translate(${width / 2}, ${height / 2})`)
  }

  const g = zoomContainer.append("g")

  const root = d3.hierarchy<TreeNode>(treeData)
  const cluster = d3.cluster<TreeNode>().size([2 * Math.PI, radius])
  const pointRoot = cluster(root)

  const linkGenerator = d3.linkRadial<d3.HierarchyPointLink<TreeNode>, d3.HierarchyPointNode<TreeNode>>()
  linkGenerator.angle((d) => d.x)
  linkGenerator.radius((d) => d.y)

  // 添加连线，初始从中心开始
  const links = g.append("g")
    .attr("fill", "none")
    .attr("stroke", "rgba(255,255,255,0.35)")
    .attr("stroke-width", 1)
    .selectAll("path")
    .data(pointRoot.links())
    .join("path")
    .attr("d", (d) => {
      // 初始状态：所有连线都从中心点开始
      const startLink = {
        source: { x: d.source.x, y: 0 },
        target: { x: d.target.x, y: 0 }
      }
      return linkGenerator(startLink as any)
    })
    .attr("opacity", 0)

  // 添加节点组，初始位置在中心
  const node = g
    .append("g")
    .selectAll("g")
    .data(pointRoot.descendants())
    .join("g")
    .attr("transform", (d) => `rotate(${(((d.x ?? 0) * 180) / Math.PI) - 90}) translate(0,0)`)

  node
    .append("circle")
    .attr("r", 0) // 初始半径为 0
    .attr("fill", (d) => {
      if (d.depth === 0) return "#f4f4f5"
      if (d.depth === 1) return "#22d3ee"
      return "#818cf8"
    })
    .attr("stroke", "rgba(255,255,255,0.6)")
    .attr("stroke-width", 0.5)

  const labels = node
    .append("text")
    .attr("dy", "0.31em")
    .attr("x", (d) => ((d.x ?? 0) < Math.PI === !d.children ? 8 : -8))
    .attr("text-anchor", (d) => ((d.x ?? 0) < Math.PI === !d.children ? "start" : "end"))
    .attr("transform", (d) => ((d.x ?? 0) >= Math.PI ? "rotate(180)" : null))
    .attr("fill", "rgba(255,255,255,0.85)")
    .attr("font-size", (d) => (d.depth <= 1 ? 12 : 10))
    .attr("opacity", 0) // 初始透明
    .text((d) => d.data.name)

  // 添加展开动画
  node
    .transition()
    .duration(800)
    .delay((d) => d.depth * 150) // 按层级延迟
    .attr("transform", (d) => `rotate(${(((d.x ?? 0) * 180) / Math.PI) - 90}) translate(${d.y ?? 0},0)`)

  node.select("circle")
    .transition()
    .duration(600)
    .delay((d) => d.depth * 150)
    .attr("r", (d) => (d.depth === 0 ? 8 : d.depth === 1 ? 6 : 4))

  labels
    .transition()
    .duration(400)
    .delay((d) => d.depth * 150 + 300)
    .attr("opacity", 1)

  links
    .transition()
    .duration(800)
    .delay((d: any) => d.target.depth * 150)
    .attr("d", (d) => linkGenerator(d as d3.HierarchyPointLink<TreeNode>))
    .attr("opacity", 1)

  return () => {
    svg.selectAll("*").remove()
    svg.on(".zoom", null)
  }
}
