import assert from "node:assert/strict"
import test from "node:test"

import type { Citation } from "@/lib/api/types"
import { citationsToPDFSources } from "./pdf-source-mapping"

test("preserves backend page value and key explanation citation fields", () => {
  const citations: Citation[] = [
    {
      source: "outpatient-layout.pdf",
      location: "p.1",
      snippet: "fallback snippet",
      chunk_id: "chunk-1",
      page_number: 1,
      page_value: "展示空间平面布局与流线关系，适合快速建立功能分区与尺度感",
      key_explanation: "门诊部功能布局优化设计需求分析",
    },
    {
      source: "equipment-layout.pdf",
      location: "p.2",
      snippet: "second fallback",
      chunk_id: "chunk-2",
      page_number: 2,
      pageValue: "后端 camelCase page value",
      keyExplanation: "后端 camelCase key explanation",
    } as Citation,
  ]

  const sources = citationsToPDFSources(citations)

  assert.equal(sources[0].pageValue, "展示空间平面布局与流线关系，适合快速建立功能分区与尺度感")
  assert.equal(sources[0].keyExplanation, "门诊部功能布局优化设计需求分析")
  assert.equal(sources[1].pageValue, "后端 camelCase page value")
  assert.equal(sources[1].keyExplanation, "后端 camelCase key explanation")
})
