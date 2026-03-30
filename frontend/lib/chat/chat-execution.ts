import type { Citation } from "@/lib/api/types"

export interface ChatExecutionResult {
  content: string
  citations: Citation[]
  images: string[]
  success: boolean
  error?: string
}

interface RunChatWithFallbackOptions {
  useBackend: boolean
  runBackendStream: () => Promise<ChatExecutionResult>
  runBackendSend: () => Promise<ChatExecutionResult>
  runMock: () => Promise<ChatExecutionResult>
}

function isConnectivityFailure(error?: string): boolean {
  const message = String(error || "").toLowerCase()
  if (!message) return false

  return [
    "failed to connect",
    "backend not available",
    "network error",
    "fetch failed",
    "econnrefused",
    "ecconnrefused",
    "connection refused",
    "connection reset",
    "socket hang up",
    "stream error:",
  ].some((marker) => message.includes(marker))
}

function isModelProviderFailure(error?: string): boolean {
  const message = String(error || "").toLowerCase()
  if (!message) return false

  return [
    "openai",
    "deepseek",
    "anthropic",
    "qwen",
    "model api",
    "llm provider",
    "upstream",
    "invalid api key",
    "api key",
    "authentication failed",
    "unauthorized",
    "forbidden",
    "rate limit",
    "quota exceeded",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "provider error",
  ].some((marker) => message.includes(marker))
}

function shouldUseMockFallback(error?: string): boolean {
  return isConnectivityFailure(error) || isModelProviderFailure(error)
}

export async function runChatWithFallback({
  useBackend,
  runBackendStream,
  runBackendSend,
  runMock,
}: RunChatWithFallbackOptions): Promise<ChatExecutionResult> {
  if (!useBackend) {
    return runMock()
  }

  const backendStreamResult = await runBackendStream()
  if (backendStreamResult.success) {
    return backendStreamResult
  }

  const backendSendResult = await runBackendSend()
  if (backendSendResult.success) {
    return backendSendResult
  }

  if (
    !shouldUseMockFallback(backendStreamResult.error) &&
    !shouldUseMockFallback(backendSendResult.error)
  ) {
    return {
      ...backendSendResult,
      error: backendStreamResult.error || backendSendResult.error,
    }
  }

  const mockResult = await runMock()
  if (mockResult.success) {
    return mockResult
  }

  return {
    ...mockResult,
    error: backendStreamResult.error || backendSendResult.error || mockResult.error,
  }
}
