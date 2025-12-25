"use client"

import { Suspense } from "react"
import dynamic from "next/dynamic"

const ChatInterface = dynamic(() => import("@/components/chat/chat-interface"), {
  ssr: false,
  loading: () => (
    <div className="flex min-h-screen items-center justify-center bg-black px-6">
      <div className="space-y-4 text-center">
        <div className="mx-auto h-16 w-16 animate-spin rounded-full border-4 border-white/10 border-t-white" />
        <p className="text-sm text-white/60">正在载入智能问答工作台...</p>
      </div>
    </div>
  ),
})

export default function ChatPage() {
  return (
    <main className="min-h-screen bg-black">
      <Suspense
        fallback={
          <div className="flex min-h-screen items-center justify-center bg-black px-6">
            <div className="space-y-4 text-center">
              <div className="mx-auto h-16 w-16 animate-spin rounded-full border-4 border-white/10 border-t-white" />
              <p className="text-sm text-white/60">正在载入智能问答工作台...</p>
            </div>
          </div>
        }
      >
        <ChatInterface />
      </Suspense>
    </main>
  )
}
