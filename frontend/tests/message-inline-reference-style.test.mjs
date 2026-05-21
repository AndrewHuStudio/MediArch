import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import jitiFactory from "../node_modules/.pnpm/jiti@2.6.1/node_modules/jiti/lib/jiti.mjs"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const source = readFileSync(join(__dirname, "..", "components", "chat", "message-with-sources.tsx"), "utf8")
const jiti = jitiFactory(import.meta.url)
const mod = jiti(join(__dirname, "..", "lib", "chat", "markdown-display.ts"))

test("message markdown renderer styles compact inline figure references", () => {
  const rendered = mod.applyInlineReferenceMarkup("见图（1-2）与参考(1-3)")
  assert.match(rendered, /data-inline-reference=\"true\"/)
  assert.match(rendered, /text-\[10px\]/)
  assert.match(rendered, /text-gray-400/)
  assert.match(source, /buildMarkdownDisplayContent/)
})
