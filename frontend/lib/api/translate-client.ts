import { API_ENDPOINTS, getApiBaseUrlCandidates, getApiUrl } from "./config"

type TranslateRequestFn = (
  url: string,
  body: { text: string; target_lang: "en" | "zh" },
) => Promise<{ translated: string }>

export async function requestTranslationWithCandidates({
  candidates = getApiBaseUrlCandidates(),
  text,
  targetLang,
  requestFn,
}: {
  candidates?: string[]
  text: string
  targetLang: "en" | "zh"
  requestFn: TranslateRequestFn
}): Promise<string> {
  let lastError: unknown = null

  for (const baseUrl of candidates) {
    try {
      const response = await requestFn(getApiUrl(API_ENDPOINTS.TRANSLATE, baseUrl), {
        text,
        target_lang: targetLang,
      })
      return response.translated
    } catch (error) {
      lastError = error
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Translation failed")
}
