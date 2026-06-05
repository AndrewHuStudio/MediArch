"use client"

import type React from "react"
import { PlaceholdersAndVanishInput } from "@/components/ui/placeholders-and-vanish-input"
import GradientButton from "@/components/ui/gradient-button"
import { Share2, Bot, Layers, ChevronDown } from "lucide-react"
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Waves } from "@/components/ui/wave-background"
import { useT } from "@/lib/i18n"

interface HeroSectionProps {
  onNavigate: (sectionIndex: number) => void
}

export default function HeroSection({ onNavigate }: HeroSectionProps) {
  const [inputValue, setInputValue] = useState("")
  const router = useRouter()
  const { t } = useT()

  useEffect(() => {
    void router.prefetch("/chat")
  }, [router])

  const placeholders = [
    t('hero.placeholder.1'),
    t('hero.placeholder.2'),
    t('hero.placeholder.3'),
    t('hero.placeholder.4'),
    t('hero.placeholder.5'),
  ]

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(e.target.value)
  }

  const handleChatClick = () => {
    router.push("/chat")
  }

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (inputValue.trim()) {
      router.push(`/chat?q=${encodeURIComponent(inputValue.trim())}`)
    } else {
      router.push("/chat")
    }
  }

  return (
    <section id="section-0" className="relative z-10 flex flex-col pt-16 overflow-hidden h-screen bg-[#f7fbfc] text-[#12323a]">
      <div className="absolute inset-0 z-0">
        <Waves
          className="w-full h-full"
          strokeColor="#b7c9d3"
          backgroundColor="#f7fbfc"
          pointerSize={0.6}
        />
      </div>

      <main className="flex-1 flex flex-col items-center justify-center px-6 relative z-10">
        <div className="w-full max-w-4xl text-center flex flex-col items-center">
          <h1 className="text-6xl md:text-[120px] font-bold mb-4 bg-gradient-to-r from-[#12323a] via-[#0e7490] to-[#8ba4ad] bg-clip-text text-transparent">
            MediArch
          </h1>

          <p className="text-xl text-[#335158] mb-12 max-w-2xl">{t('hero.subtitle')}</p>

          <div className="mb-8 max-w-2xl w-full">
            <div className="relative p-[2px] rounded-full overflow-hidden">
              <div
                className="absolute inset-0 rounded-full"
                style={{
                  background: "linear-gradient(90deg, #0e7490, #7dd3fc, #059669, #cfe2e7, #7dd3fc, #0e7490)",
                  backgroundSize: "400% 100%",
                  animation: "rainbow-border 20s linear infinite",
                  borderRadius: "9999px",
                }}
              />
              <div
                className="absolute inset-0 rounded-full blur-md opacity-75"
                style={{
                  background: "linear-gradient(90deg, #0e7490, #7dd3fc, #059669, #cfe2e7, #7dd3fc, #0e7490)",
                  backgroundSize: "400% 100%",
                  animation: "rainbow-border 20s linear infinite",
                  borderRadius: "9999px",
                }}
              />
              <div className="relative bg-white rounded-full">
                <PlaceholdersAndVanishInput placeholders={placeholders} onChange={handleChange} onSubmit={onSubmit} />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12 w-full max-w-3xl">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="w-8 h-8 rounded-full bg-white/85 border border-[#cfe2e7] shadow-sm flex items-center justify-center">
                <Share2 className="w-4 h-4 text-[#0e7490]" />
              </div>
              <GradientButton width="160px" height="36px" onClick={() => onNavigate(2)}>
                <span className="text-[#12323a] font-medium text-sm">{t('hero.btn.graph')}</span>
              </GradientButton>
              <p className="text-[#516b72] text-xs leading-relaxed max-w-48">
                {t('hero.desc.graph')}
              </p>
            </div>

            <div className="flex flex-col items-center gap-3 text-center">
              <div className="w-8 h-8 rounded-full bg-white/85 border border-[#cfe2e7] shadow-sm flex items-center justify-center">
                <Bot className="w-4 h-4 text-[#059669]" />
              </div>
              <GradientButton width="140px" height="36px" onClick={handleChatClick}>
                <span className="text-[#12323a] font-medium text-sm">{t('hero.btn.chat')}</span>
              </GradientButton>
              <p className="text-[#516b72] text-xs leading-relaxed max-w-48">
                {t('hero.desc.chat')}
              </p>
            </div>

            <div className="flex flex-col items-center gap-3 text-center">
              <div className="w-8 h-8 rounded-full bg-white/85 border border-[#cfe2e7] shadow-sm flex items-center justify-center">
                <Layers className="w-4 h-4 text-[#2563eb]" />
              </div>
              <GradientButton width="160px" height="36px" onClick={() => onNavigate(1)}>
                <span className="text-[#12323a] font-medium text-sm">{t('hero.btn.search')}</span>
              </GradientButton>
              <p className="text-[#516b72] text-xs leading-relaxed max-w-48">
                {t('hero.desc.search')}
              </p>
            </div>
          </div>
        </div>
      </main>

      <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 flex flex-col items-center gap-2 relative z-10">
        <button
          onClick={() => onNavigate(1)}
          data-nav-button
          className="text-[#6c858c] hover:text-[#0e7490] transition-colors animate-bounce"
        >
          <ChevronDown className="w-6 h-6" />
        </button>
      </div>
    </section>
  )
}
