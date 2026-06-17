import assert from "node:assert/strict"
import test from "node:test"

import { buildMarkdownDisplayContent } from "./markdown-display"

test("removes image placeholders when the image URL is not available yet", () => {
  const rendered = buildMarkdownDisplayContent("说明文字。\n\n[image:0]\n\n后续文字。", 0, 0)

  assert.equal(rendered.includes("data-chat-image-index"), false)
  assert.equal(rendered.includes("[image:0]"), false)
  assert.match(rendered, /说明文字。/)
  assert.match(rendered, /后续文字。/)
})

test("keeps image placeholders when the image URL is available", () => {
  const rendered = buildMarkdownDisplayContent("说明文字。\n\n[image:0]", 0, 1)

  assert.match(rendered, /<figure data-chat-image-index="0"><\/figure>/)
})

test("renders citations with high contrast colors", () => {
  const rendered = buildMarkdownDisplayContent("诊室应保障私密性 [1]。", 1, 0)

  assert.match(rendered, /data-citation="1"/)
  assert.match(rendered, /text-\[#034b63\]/)
  assert.match(rendered, /border-\[#0891b2\]/)
})
