"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { motion } from "framer-motion"
import { Network } from "lucide-react"
import { KnowledgeGraphD3, type GraphData } from "@/components/ui/knowledge-graph-d3"
import { translateText } from "@/lib/api"
import {
  applyGraphTranslations,
  collectGraphTexts,
} from "@/lib/chat/knowledge-graph-translation"
import { useT } from "@/lib/i18n"

interface KnowledgeGraphPanelProps {
  graphData: GraphData
  isAnimating: boolean
}

export default function KnowledgeGraphPanel({ graphData, isAnimating }: KnowledgeGraphPanelProps) {
  const { t } = useT()
  const [displayLanguage, setDisplayLanguage] = useState<"zh" | "en">("zh")
  const [isTranslating, setIsTranslating] = useState(false)
  const [translationCache, setTranslationCache] = useState<Record<string, string>>({})

  const ensureEnglishTranslations = useCallback(async () => {
    const texts = collectGraphTexts(graphData)
    const missingTexts = texts.filter((text) => !translationCache[text])
    if (missingTexts.length === 0) return

    setIsTranslating(true)
    try {
      const translatedEntries = await Promise.all(
        missingTexts.map(async (text) => [text, await translateText(text, "en")] as const),
      )

      setTranslationCache((prev) => ({
        ...prev,
        ...Object.fromEntries(translatedEntries),
      }))
    } finally {
      setIsTranslating(false)
    }
  }, [graphData, translationCache])

  useEffect(() => {
    if (displayLanguage !== "en" || graphData.nodes.length === 0) return
    void ensureEnglishTranslations()
  }, [displayLanguage, ensureEnglishTranslations, graphData.nodes.length])

  const displayGraphData = useMemo(() => {
    if (displayLanguage !== "en") return graphData
    return applyGraphTranslations(graphData, translationCache)
  }, [displayLanguage, graphData, translationCache])

  const handleToggleLanguage = useCallback(async () => {
    const nextLanguage = displayLanguage === "en" ? "zh" : "en"
    if (nextLanguage === "zh") {
      setDisplayLanguage("zh")
      return
    }

    try {
      await ensureEnglishTranslations()
      setDisplayLanguage("en")
    } catch (error) {
      console.warn("Failed to translate knowledge graph:", error)
      setDisplayLanguage("zh")
    }
  }, [displayLanguage, ensureEnglishTranslations])

  const queryPath = useMemo(() => {
    if (!displayGraphData?.nodes?.length) return ""
    // 使用 schema 定义的节点类型
    const hospital = displayGraphData.nodes.find((n) => n.type === "Hospital")?.label
    const department = displayGraphData.nodes.find((n) => n.type === "DepartmentGroup")?.label
    const zone = displayGraphData.nodes.find((n) => n.type === "FunctionalZone")?.label
    const space = displayGraphData.nodes.find((n) => n.type === "Space")?.label
    const parts = [hospital, department, zone, space].filter(Boolean) as string[]
    return parts.length >= 2 ? parts.join(" → ") : ""
  }, [displayGraphData])

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
          <h3 className="text-sm font-semibold text-white">{t('chat.kgPanel.title')}</h3>
        </div>
        <div className="flex items-center gap-2">
          {isTranslating && (
            <span className="text-[11px] text-gray-400">{t("translate.translating")}</span>
          )}
          <button
            type="button"
            onClick={() => void handleToggleLanguage()}
            disabled={isTranslating || graphData.nodes.length === 0}
            className="rounded-full border border-white/20 bg-black/60 px-2.5 py-1 text-xs text-gray-200 transition-colors hover:border-white/40 hover:text-white disabled:cursor-wait disabled:opacity-70"
            title={displayLanguage === "en" ? t("translate.toChinese") : t("translate.toEnglish")}
          >
            中 / EN
          </button>
        </div>
      </div>

      {queryPath && (
        <div className="mb-3">
          <div className="text-[11px] text-gray-300">
            <span className="text-gray-400">{t('chat.kgPanel.queryPath')}</span>
            {queryPath}
          </div>
        </div>
      )}

      <div className="flex-1 min-h-0">
        {displayGraphData.nodes.length > 0 ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="h-full w-full"
          >
            <KnowledgeGraphD3 data={displayGraphData} isAnimating={isAnimating} />
          </motion.div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="space-y-4 text-center">
              <div className="relative mx-auto h-48 w-48">
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-yellow-500/50 bg-yellow-500/20">
                    <span className="text-xs text-yellow-300">{t('chat.kgPanel.coreConcept')}</span>
                  </div>
                </div>
                <div className="absolute left-1/2 top-0 -translate-x-1/2">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-blue-500/50 bg-blue-500/20">
                    <span className="text-[10px] text-blue-300">{t('chat.kgPanel.node1')}</span>
                  </div>
                </div>
                <div className="absolute bottom-0 left-0">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-green-500/50 bg-green-500/20">
                    <span className="text-[10px] text-green-300">{t('chat.kgPanel.node2')}</span>
                  </div>
                </div>
                <div className="absolute bottom-0 right-0">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-purple-500/50 bg-purple-500/20">
                    <span className="text-[10px] text-purple-300">{t('chat.kgPanel.node3')}</span>
                  </div>
                </div>
              </div>
              <p className="text-xs text-gray-400">{t('chat.kgPanel.empty')}</p>
            </div>
          </div>
        )}
      </div>
    </motion.div>
  )
}
