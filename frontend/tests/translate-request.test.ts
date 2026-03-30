import test from "node:test"
import assert from "node:assert/strict"

import { requestTranslationWithCandidates } from "../lib/api/translate-client"

test("translation retries direct API candidates without relying on health probing", async () => {
  const calls: string[] = []

  const translated = await requestTranslationWithCandidates({
    candidates: ["http://127.0.0.1:9999", "http://127.0.0.1:8010"],
    text: "综合医院",
    targetLang: "en",
    requestFn: async (url, body) => {
      calls.push(url)
      if (url.includes(":9999")) {
        throw new Error("connection refused")
      }
      assert.deepEqual(body, { text: "综合医院", target_lang: "en" })
      return { translated: "General Hospital" }
    },
  })

  assert.equal(translated, "General Hospital")
  assert.deepEqual(calls, [
    "http://127.0.0.1:9999/api/v1/chat/translate",
    "http://127.0.0.1:8010/api/v1/chat/translate",
  ])
})
