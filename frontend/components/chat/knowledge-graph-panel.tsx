"use client"

import { useMemo } from "react"
import { motion } from "framer-motion"
import { Network } from "lucide-react"
import { KnowledgeGraphD3, type GraphData } from "@/components/ui/knowledge-graph-d3"

interface KnowledgeGraphPanelProps {
  graphData: GraphData
  isAnimating: boolean
}

export default function KnowledgeGraphPanel({ graphData, isAnimating }: KnowledgeGraphPanelProps) {
  const queryPath = useMemo(() => {
    if (!graphData?.nodes?.length) return ""
    // 使用 schema 定义的节点类型
    const hospital = graphData.nodes.find((n) => n.type === "Hospital")?.label
    const department = graphData.nodes.find((n) => n.type === "DepartmentGroup")?.label
    const zone = graphData.nodes.find((n) => n.type === "FunctionalZone")?.label
    const space = graphData.nodes.find((n) => n.type === "Space")?.label
    const parts = [hospital, department, zone, space].filter(Boolean) as string[]
    return parts.length >= 2 ? parts.join(" → ") : ""
  }, [graphData])

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
      className="flex h-full flex-col rounded-lg border border-white/10 bg-black/40 p-4 backdrop-blur-md"
    >
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Network className="h-5 w-5 text-yellow-400" />
          <h3 className="text-sm font-semibold text-white">知识图谱</h3>
        </div>
      </div>

      {queryPath && (
        <div className="mb-3">
          <div className="text-[11px] text-gray-300">
            <span className="text-gray-400">查询路径：</span>
            {queryPath}
          </div>
        </div>
      )}

      <div className="flex-1 min-h-0">
        {graphData.nodes.length > 0 ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="h-full w-full"
          >
            <KnowledgeGraphD3 data={graphData} isAnimating={isAnimating} />
          </motion.div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="space-y-4 text-center">
              <div className="relative mx-auto h-48 w-48">
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-yellow-500/50 bg-yellow-500/20">
                    <span className="text-xs text-yellow-300">核心概念</span>
                  </div>
                </div>
                <div className="absolute left-1/2 top-0 -translate-x-1/2">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-blue-500/50 bg-blue-500/20">
                    <span className="text-[10px] text-blue-300">节点1</span>
                  </div>
                </div>
                <div className="absolute bottom-0 left-0">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-green-500/50 bg-green-500/20">
                    <span className="text-[10px] text-green-300">节点2</span>
                  </div>
                </div>
                <div className="absolute bottom-0 right-0">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-purple-500/50 bg-purple-500/20">
                    <span className="text-[10px] text-purple-300">节点3</span>
                  </div>
                </div>
              </div>
              <p className="text-xs text-gray-400">查询结果的知识图谱将在此展示</p>
            </div>
          </div>
        )}
      </div>
    </motion.div>
  )
}
