import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

test("message source list does not eagerly generate PDF thumbnails from pdf.js", () => {
  const source = readFileSync(join(__dirname, "..", "components", "chat", "message-with-sources.tsx"), "utf8")

  assert.doesNotMatch(source, /getPdfThumbnail\(/)
  assert.doesNotMatch(source, /loadThumbnails/)
})

test("pdf viewer mounts Document only when modal is open and pdfUrl exists", () => {
  const source = readFileSync(join(__dirname, "..", "components", "chat", "pdf-viewer-modal.tsx"), "utf8")

  assert.match(source, /isOpen && \(/)
  assert.match(source, /source\.pdfUrl && \(/)
  assert.match(source, /devicePixelRatio=\{/)
})
