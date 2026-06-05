"use client"

import { Suspense } from "react"
import dynamic from "next/dynamic"
import { useT } from "@/lib/i18n"

function LoadingSpinner() {
  const { t } = useT()
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f7fbfc] px-6">
      <div className="space-y-4 text-center">
        <div className="mx-auto h-16 w-16 animate-spin rounded-full border-4 border-[#d9e7eb] border-t-[#0e7490]" />
        <p className="text-sm text-[#516b72]">{t('chat.loading')}</p>
      </div>
    </div>
  )
}

const ChatInterface = dynamic(() => import("@/components/chat/chat-interface"), {
  ssr: false,
  loading: () => <LoadingSpinner />,
})

export default function ChatPage() {
  return (
    <main className="min-h-screen bg-[#f7fbfc] text-[#12323a]">
      <Suspense fallback={<LoadingSpinner />}>
        <ChatInterface />
      </Suspense>
    </main>
  )
}
