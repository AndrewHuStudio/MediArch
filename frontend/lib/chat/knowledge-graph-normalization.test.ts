import assert from "node:assert/strict"
import test from "node:test"

import { convertKnowledgeGraphData } from "./knowledge-graph-normalization"

test("uses backend name as display label while keeping backend label as node type", () => {
  const graph = convertKnowledgeGraphData({
    nodes: [
      { id: "10", label: "Source", name: "医院建筑设计指南.pdf" },
      { id: "11", label: "Case", name: "既有大型综合医院门诊部功能布局优化设计研究_员俊.pdf" },
    ],
    edges: [
      { source: "11", target: "10", relation: "MENTIONED_IN" },
    ],
  })

  assert.deepEqual(graph.nodes, [
    { id: "10", label: "医院建筑设计指南.pdf", type: "Source" },
    { id: "11", label: "既有大型综合医院门诊部功能布局优化设计研究_员俊.pdf", type: "Case" },
  ])
  assert.deepEqual(graph.links, [
    { source: "11", target: "10", label: "MENTIONED_IN" },
  ])
})

test("supports older payloads where type already stores the node category", () => {
  const graph = convertKnowledgeGraphData({
    nodes: [
      { id: "room-1", label: "病房", type: "Space" },
    ],
    links: [],
  })

  assert.deepEqual(graph.nodes, [
    { id: "room-1", label: "病房", type: "Space" },
  ])
})

test("merges duplicate source nodes by semantic identity and remaps edges", () => {
  const graph = convertKnowledgeGraphData({
    nodes: [
      { id: "doc-a", name: "医院建筑设计指南.pdf", type: "Source" },
      { id: "doc-b", label: "医院建筑设计指南.pdf" },
      { id: "kp-1", name: "病房布置原则", type: "KnowledgePoint" },
      { id: "space-1", name: "病房", type: "Space" },
    ],
    edges: [
      { source: "kp-1", target: "doc-a", relation: "MENTIONED_IN" },
      { source: "doc-b", target: "space-1", relation: "REFERENCES" },
    ],
  })

  assert.equal(
    graph.nodes.filter((node) => node.label === "医院建筑设计指南.pdf" && node.type === "Source").length,
    1,
  )
  assert.deepEqual(graph.links, [
    { source: "kp-1", target: "doc-a", label: "MENTIONED_IN" },
    { source: "doc-a", target: "space-1", label: "REFERENCES" },
  ])
})

test("keeps edges when backend returns numeric node ids", () => {
  const graph = convertKnowledgeGraphData({
    nodes: [
      { id: "101", name: "住院部", type: "DepartmentGroup" },
      { id: "102", name: "护理单元", type: "FunctionalZone" },
    ],
    edges: [
      { source: 101 as unknown as string, target: 102 as unknown as string, relation: "CONTAINS" },
    ],
  })

  assert.deepEqual(graph.links, [
    { source: "101", target: "102", label: "CONTAINS" },
  ])
})

test("preserves synthetic bridge edges and knowledge-point node types", () => {
  const graph = convertKnowledgeGraphData({
    nodes: [
      { id: "space-1", name: "候诊区", type: "Space" },
      { id: "kp-1", name: "候诊区流线原则", type: "KnowledgePoint" },
    ],
    edges: [
      {
        source: "space-1",
        target: "kp-1",
        relation: "BRIDGED_TO",
      },
    ],
  })

  assert.deepEqual(graph.nodes, [
    { id: "space-1", label: "候诊区", type: "Space" },
    { id: "kp-1", label: "候诊区流线原则", type: "KnowledgePoint" },
  ])
  assert.deepEqual(graph.links, [
    {
      source: "space-1",
      target: "kp-1",
      label: "BRIDGED_TO",
      isSynthetic: true,
      isVisualBridge: true,
    },
  ])
})
