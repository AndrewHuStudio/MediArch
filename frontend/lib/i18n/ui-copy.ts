import { en } from "./en"
import type { Locale } from "./types"
import { zh } from "./zh"

const dicts = { zh, en } as const

export type Translator = (key: string, params?: Record<string, string | number>) => string

export function createTranslator(locale: Locale): Translator {
  return (key, params) => {
    let text = dicts[locale][key] ?? dicts.zh[key] ?? key
    if (params) {
      Object.entries(params).forEach(([paramKey, value]) => {
        text = text.replace(new RegExp(`\\{${paramKey}\\}`, "g"), String(value))
      })
    }
    return text
  }
}

export function getLandingNavItems(t: Translator) {
  return [
    { key: "home", label: t("nav.home"), index: 0 },
    { key: "knowledgeBase", label: t("nav.knowledgeBase"), index: 1 },
    { key: "knowledgeGraph", label: t("nav.knowledgeGraph"), index: 2 },
    { key: "lab", label: t("nav.lab"), index: 3 },
  ] as const
}

export function getChatAgentDefinitions(t: Translator) {
  return [
    {
      id: "orchestrator",
      backendNames: ["Orchestrator", "orchestrator", "orchestrator_agent", "Orchestrator Agent"],
      label: t("agent.label.orchestrator"),
      thoughts: [
        t("agent.thoughts.orchestrator.1"),
        t("agent.thoughts.orchestrator.2"),
        t("agent.thoughts.orchestrator.3"),
      ],
    },
    {
      id: "neo4j",
      backendNames: ["Neo4j", "neo4j", "neo4j_agent", "Neo4j Agent"],
      label: t("agent.label.neo4j"),
      thoughts: [t("agent.thoughts.neo4j.1"), t("agent.thoughts.neo4j.2"), t("agent.thoughts.neo4j.3")],
    },
    {
      id: "milvus",
      backendNames: ["Milvus", "milvus", "milvus_agent", "Milvus Agent"],
      label: t("agent.label.milvus"),
      thoughts: [t("agent.thoughts.milvus.1"), t("agent.thoughts.milvus.2"), t("agent.thoughts.milvus.3")],
    },
    {
      id: "mongodb",
      backendNames: ["MongoDB", "mongodb", "mongodb_agent", "MongoDB Agent"],
      label: t("agent.label.mongodb"),
      thoughts: [t("agent.thoughts.mongodb.1"), t("agent.thoughts.mongodb.2"), t("agent.thoughts.mongodb.3")],
    },
    {
      id: "onlineSearch",
      backendNames: ["OnlineSearch", "online_search", "online_search_agent", "Online Search Agent"],
      label: t("agent.label.online"),
      thoughts: [t("agent.thoughts.online.1"), t("agent.thoughts.online.2"), t("agent.thoughts.online.3")],
    },
    {
      id: "synthesizer",
      backendNames: ["Synthesizer", "synthesizer", "result_synthesizer", "result_synthesizer_agent", "Result Synthesizer Agent"],
      label: t("agent.label.synthesizer"),
      thoughts: [
        t("agent.thoughts.synthesizer.1"),
        t("agent.thoughts.synthesizer.2"),
        t("agent.thoughts.synthesizer.3"),
      ],
    },
  ] as const
}

export function formatConversationTimestamp(date: Date, locale: Locale, t: Translator) {
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / (1000 * 60))
  const hours = Math.floor(diff / (1000 * 60 * 60))
  const days = Math.floor(diff / (1000 * 60 * 60 * 24))

  if (minutes < 1) return t("chat.time.justNow")
  if (minutes < 60) return locale === "zh" ? `${minutes}${t("chat.time.minutesAgo")}` : `${minutes}${t("chat.time.minutesAgo")}`
  if (hours < 24) return locale === "zh" ? `${hours}${t("chat.time.hoursAgo")}` : `${hours}${t("chat.time.hoursAgo")}`
  if (days < 7) return locale === "zh" ? `${days}${t("chat.time.daysAgo")}` : `${days}${t("chat.time.daysAgo")}`

  return date.toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
  })
}

export function getKnowledgeGraphNodeTypeItems(t: Translator) {
  return [
    { type: "Hospital", label: t("graph.nodeType.hospital"), color: "#8B7355" },
    { type: "DepartmentGroup", label: t("graph.nodeType.department"), color: "#5B7FA8" },
    { type: "FunctionalZone", label: t("graph.nodeType.zone"), color: "#7B68A8" },
    { type: "Space", label: t("graph.nodeType.space"), color: "#5A9B7D" },
    { type: "DesignMethod", label: t("graph.nodeType.designMethod"), color: "#C17A4F" },
    { type: "DesignMethodCategory", label: t("graph.nodeType.designCategory"), color: "#A89968" },
    { type: "KnowledgePoint", label: t("graph.nodeType.knowledgePoint"), color: "#B8A858" },
    { type: "Case", label: t("graph.nodeType.case"), color: "#C97B9E" },
    { type: "Source", label: t("graph.nodeType.source"), color: "#B85C6F" },
    { type: "MedicalService", label: t("graph.nodeType.medicalService"), color: "#8B7BA8" },
    { type: "MedicalEquipment", label: t("graph.nodeType.medicalEquipment"), color: "#6B8BA8" },
    { type: "TreatmentMethod", label: t("graph.nodeType.treatmentMethod"), color: "#9B7BA8" },
  ] as const
}
