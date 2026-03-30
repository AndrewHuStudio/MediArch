import type { GraphData } from "@/components/ui/knowledge-graph-d3"

export type TranslatableGraphData = GraphData

export function collectGraphTexts(graph: TranslatableGraphData): string[] {
  const seen = new Set<string>()
  const texts: string[] = []

  const addText = (value: string) => {
    const normalized = value.trim()
    if (!normalized || seen.has(normalized)) return
    seen.add(normalized)
    texts.push(normalized)
  }

  graph.nodes.forEach((node) => addText(node.label))
  graph.links.forEach((link) => addText(link.label))

  return texts
}

export function applyGraphTranslations(
  graph: TranslatableGraphData,
  translations: Record<string, string>,
): TranslatableGraphData {
  const translate = (value: string) => translations[value] || value

  return {
    nodes: graph.nodes.map((node) => ({
      ...node,
      label: translate(node.label),
    })),
    links: graph.links.map((link) => ({
      ...link,
      label: translate(link.label),
    })),
  }
}
