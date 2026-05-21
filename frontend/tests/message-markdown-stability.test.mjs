import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const source = readFileSync(join(__dirname, "..", "components", "chat", "message-with-sources.tsx"), "utf8")

test("final markdown renderer does not redistribute citation clusters before markdown parse", () => {
  assert.doesNotMatch(source, /distributeCitationClusters\(/)
  assert.doesNotMatch(source, /distributeTrailingCitations\(/)
})

test("streaming and final answer paths both use MarkdownContent", () => {
  const chatMessages = readFileSync(join(__dirname, "..", "components", "chat", "chat-messages.tsx"), "utf8")
  assert.match(chatMessages, /<AssistantMessageContent content=\{streamingMessage\}/)
  assert.match(chatMessages, /<MessageWithSources/)
})
