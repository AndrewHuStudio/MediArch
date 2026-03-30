import test from "node:test"
import assert from "node:assert/strict"

import { en } from "../lib/i18n/en"
import { zh } from "../lib/i18n/zh"
import { createTranslator, getChatAgentDefinitions, getLandingNavItems } from "../lib/i18n/ui-copy"

test("landing nav items are localized", () => {
  const zhT = createTranslator("zh")
  const enT = createTranslator("en")

  assert.deepEqual(
    getLandingNavItems(zhT).map((item) => item.label),
    ["首页", "知识库", "知识图谱", "实验室"],
  )

  assert.deepEqual(
    getLandingNavItems(enT).map((item) => item.label),
    ["Home", "Knowledge Base", "Knowledge Graph", "Lab"],
  )
})

test("chat agent definitions use localized labels and thought copy", () => {
  const zhT = createTranslator("zh")
  const enT = createTranslator("en")

  const zhAgents = getChatAgentDefinitions(zhT)
  const enAgents = getChatAgentDefinitions(enT)

  assert.equal(zhAgents[0].label, "协调智能体")
  assert.equal(enAgents[0].label, "Orchestrator Agent")

  assert.deepEqual(zhAgents[1].thoughts, [
    zh["agent.thoughts.neo4j.1"],
    zh["agent.thoughts.neo4j.2"],
    zh["agent.thoughts.neo4j.3"],
  ])

  assert.deepEqual(enAgents[5].thoughts, [
    en["agent.thoughts.synthesizer.1"],
    en["agent.thoughts.synthesizer.2"],
    en["agent.thoughts.synthesizer.3"],
  ])
})
