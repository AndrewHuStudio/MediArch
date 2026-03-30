import type { GraphData } from "@/components/ui/knowledge-graph-d3"

type RawGraphNode = {
  id?: string | number
  name?: string
  label?: string
  type?: string
}

type RawGraphLink = {
  source?: string | number
  target?: string | number
  label?: string
  relation?: string
  properties?: Record<string, unknown>
}

type RawKnowledgeGraphData = {
  nodes?: RawGraphNode[]
  links?: RawGraphLink[]
  edges?: RawGraphLink[]
}

export function mapKnowledgeGraphNodeType(type: string | undefined): string {
  if (!type) return "entity"

  const schemaTypes = [
    "Hospital", "DepartmentGroup", "FunctionalZone", "Space",
    "DesignMethod", "DesignMethodCategory", "Case", "Source",
    "MedicalService", "MedicalEquipment", "TreatmentMethod",
    "KnowledgePoint",
  ]

  if (schemaTypes.includes(type)) {
    return type
  }

  const typeLower = type.toLowerCase()

  if (typeLower.includes("hospital")) return "Hospital"
  if (typeLower.includes("department")) return "DepartmentGroup"
  if (typeLower.includes("zone") || typeLower.includes("功能分区")) return "FunctionalZone"
  if (typeLower.includes("space") || typeLower.includes("room")) return "Space"
  if (typeLower.includes("design") && typeLower.includes("method")) return "DesignMethod"
  if (typeLower.includes("case") || typeLower.includes("案例")) return "Case"
  if (typeLower.includes("knowledge") || typeLower.includes("知识")) return "KnowledgePoint"
  if (typeLower.includes("document") || typeLower.includes("source") || typeLower.includes("_doc")) return "Source"

  return "entity"
}

function isSchemaNodeType(value: string | undefined): boolean {
  if (!value) return false
  return mapKnowledgeGraphNodeType(value) !== "entity" || value === "entity"
}

function looksLikeSourceLabel(value: string | undefined): boolean {
  if (!value) return false
  const normalized = value.trim().toLowerCase()
  return (
    normalized.endsWith(".pdf")
    || normalized.endsWith(".doc")
    || normalized.endsWith(".docx")
    || normalized.endsWith(".txt")
    || normalized.includes("规范")
    || normalized.includes("指南")
    || normalized.includes("图集")
    || normalized.includes("研究")
    || normalized.includes("标准")
  )
}

function cleanDisplayLabel(value: string, type: string): string {
  const trimmed = value.trim()
  const prefixes = [type, "Source", "Space", "Case", "KnowledgePoint"]
  for (const prefix of prefixes) {
    if (trimmed.startsWith(prefix) && trimmed.length > prefix.length) {
      return trimmed.slice(prefix.length).trim() || trimmed
    }
  }
  return trimmed
}

function canonicalizeNode(
  node: RawGraphNode,
): { rawId: string | null; canonicalLabel: string; canonicalType: string; displayLabel: string } | null {
  const rawId = node.id == null ? null : String(node.id)
  const nodeLabelIsType = isSchemaNodeType(node.label)
  const rawType = node.type || (nodeLabelIsType ? node.label : undefined)
  const baseDisplayLabel = node.name || (!nodeLabelIsType ? node.label : undefined) || rawId || "未知"
  const mappedType = mapKnowledgeGraphNodeType(rawType)
  const inferredType = mappedType !== "entity"
    ? mappedType
    : (looksLikeSourceLabel(baseDisplayLabel) ? "Source" : mappedType)
  const displayLabel = cleanDisplayLabel(baseDisplayLabel, inferredType)
  return {
    rawId,
    canonicalLabel: displayLabel.toLowerCase(),
    canonicalType: inferredType,
    displayLabel,
  }
}

export function convertKnowledgeGraphData(data: RawKnowledgeGraphData | null): GraphData {
  if (!data) return { nodes: [], links: [] }

  const rawNodes = data.nodes || []
  const rawLinks = data.links || data.edges || []

  const nodeMap = new Map<string, GraphData["nodes"][number]>()
  const semanticNodeKeyToId = new Map<string, string>()
  const rawIdAliasMap = new Map<string, string>()
  for (const node of rawNodes) {
    const normalized = canonicalizeNode(node)
    if (!normalized) continue

    const fallbackId = normalized.rawId || `${normalized.canonicalType}:${normalized.canonicalLabel}`
    const semanticKey = `${normalized.canonicalType}:${normalized.canonicalLabel}`
    const canonicalId = semanticNodeKeyToId.get(semanticKey) || fallbackId

    semanticNodeKeyToId.set(semanticKey, canonicalId)
    if (normalized.rawId) {
      rawIdAliasMap.set(normalized.rawId, canonicalId)
    }

    if (!nodeMap.has(canonicalId)) {
      nodeMap.set(canonicalId, {
        id: canonicalId,
        label: normalized.displayLabel,
        type: normalized.canonicalType,
      })
    }
  }

  const validLinks: GraphData["links"] = []
  for (const rawLink of rawLinks) {
    const source = rawLink.source == null ? undefined : String(rawLink.source)
    const target = rawLink.target == null ? undefined : String(rawLink.target)
    const label = rawLink.label || rawLink.relation || ""

    if (!source || !target) continue

    const mappedSource = rawIdAliasMap.get(source) || source
    const mappedTarget = rawIdAliasMap.get(target) || target
    if (!nodeMap.has(mappedSource) || !nodeMap.has(mappedTarget)) continue
    if (mappedSource === mappedTarget) continue

    validLinks.push({
      source: mappedSource,
      target: mappedTarget,
      label,
      ...(Boolean(rawLink.properties?.synthetic) || label === "BRIDGED_TO"
        ? { isSynthetic: true }
        : {}),
      ...(Boolean(rawLink.properties?.visual_bridge) || label === "BRIDGED_TO"
        ? { isVisualBridge: true }
        : {}),
      ...(rawLink.properties ? { properties: rawLink.properties } : {}),
    })
  }

  const uniqueLinks = new Map<string, GraphData["links"][number]>()
  for (const link of validLinks) {
    const key = `${link.source}:${link.label}:${link.target}`
    if (!uniqueLinks.has(key)) {
      uniqueLinks.set(key, link)
    }
  }

  return {
    nodes: Array.from(nodeMap.values()),
    links: Array.from(uniqueLinks.values()),
  }
}
