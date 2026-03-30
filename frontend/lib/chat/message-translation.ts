export type AssistantDisplayLanguage = "zh" | "en"

export interface AssistantMessageTranslationState {
  content: string
  translatedContent?: string
  displayLanguage?: AssistantDisplayLanguage
}

export function getAssistantMessageDisplayContent(message: AssistantMessageTranslationState): string {
  if (message.displayLanguage === "en" && message.translatedContent) {
    return message.translatedContent
  }

  return message.content
}

export function getNextAssistantDisplayLanguage(
  message: AssistantMessageTranslationState,
): AssistantDisplayLanguage {
  return message.displayLanguage === "en" ? "zh" : "en"
}

export function shouldRequestAssistantTranslation(message: AssistantMessageTranslationState): boolean {
  return !message.translatedContent
}
