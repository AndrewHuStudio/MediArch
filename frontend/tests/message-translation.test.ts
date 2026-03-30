import test from "node:test"
import assert from "node:assert/strict"

import {
  getAssistantMessageDisplayContent,
  getNextAssistantDisplayLanguage,
  shouldRequestAssistantTranslation,
  type AssistantMessageTranslationState,
} from "../lib/chat/message-translation"

test("assistant messages default to the original Chinese content", () => {
  const message: AssistantMessageTranslationState = {
    content: "这是中文回答",
  }

  assert.equal(getAssistantMessageDisplayContent(message), "这是中文回答")
  assert.equal(getNextAssistantDisplayLanguage(message), "en")
  assert.equal(shouldRequestAssistantTranslation(message), true)
})

test("assistant messages use cached English translation when switched to English", () => {
  const message: AssistantMessageTranslationState = {
    content: "这是中文回答",
    translatedContent: "This is the English answer.",
    displayLanguage: "en",
  }

  assert.equal(getAssistantMessageDisplayContent(message), "This is the English answer.")
  assert.equal(getNextAssistantDisplayLanguage(message), "zh")
  assert.equal(shouldRequestAssistantTranslation(message), false)
})

test("assistant messages keep showing the original content until translation is available", () => {
  const message: AssistantMessageTranslationState = {
    content: "这是中文回答",
    displayLanguage: "en",
  }

  assert.equal(getAssistantMessageDisplayContent(message), "这是中文回答")
  assert.equal(shouldRequestAssistantTranslation(message), true)
})
