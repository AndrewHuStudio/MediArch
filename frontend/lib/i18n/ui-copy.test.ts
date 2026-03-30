import assert from "node:assert/strict"
import test from "node:test"

import { getKnowledgeGraphNodeTypeItems } from "./ui-copy"

test("includes knowledge-point node type in graph legend items", () => {
  const t = (key: string) => key

  const items = getKnowledgeGraphNodeTypeItems(t)

  assert.ok(items.some((item) => item.type === "KnowledgePoint"))
})
