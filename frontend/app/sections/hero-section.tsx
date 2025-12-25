"use client"

import type React from "react"
import { PlaceholdersAndVanishInput } from "@/components/ui/placeholders-and-vanish-input"
import GradientButton from "@/components/ui/gradient-button"
import { Share2, Bot, Layers, ChevronDown } from "lucide-react"
import { useState } from "react"
import { Waves } from "@/components/ui/wave-background"
import { usePageTransition } from "@/components/page-transition"

interface HeroSectionProps {
  onNavigate: (sectionIndex: number) => void
}

export default function HeroSection({ onNavigate }: HeroSectionProps) {
  const [inputValue, setInputValue] = useState("")
  const { startTransition, isTransitioning } = usePageTransition()

  const placeholders = [
    "医院手术室的设计标准是什么？",
    "如何规划病房的空间布局？",
    "医疗设备的配置要求有哪些？",
    "感染控制设计的关键要点？",
    "无障碍设计在医院中的应用？",
  ]

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(e.target.value)
  }

  const handleChatClick = () => {
    startTransition("/chat")
  }

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (inputValue.trim()) {
      startTransition(`/chat?q=${encodeURIComponent(inputValue.trim())}`)
    } else {
      startTransition("/chat")
    }
  }

  return (
    <section id="section-0" className="relative z-10 flex flex-col pt-16 overflow-hidden h-screen">
      <div className="absolute inset-0 z-0">
        <Waves
          className="w-full h-full"
          strokeColor="#888888"
          backgroundColor="#000000"
          pointerSize={0.6}
          paused={isTransitioning}
        />
      </div>

      <main className="flex-1 flex flex-col items-center justify-center px-6 relative z-10">
        <div className="w-full max-w-4xl text-center flex flex-col items-center">
          <h1 className="text-6xl md:text-[120px] font-bold mb-4 bg-gradient-to-r from-white via-gray-300 to-gray-500 bg-clip-text text-transparent">
            MediArch
          </h1>

          <p className="text-xl text-white mb-12 max-w-2xl">综合医院设计问答助手 - 专业的医疗建筑设计咨询服务</p>

          <div className="mb-8 max-w-2xl w-full">
            <div className="relative p-[2px] rounded-full overflow-hidden">
              <div
                className="absolute inset-0 rounded-full"
                style={{
                  background: "linear-gradient(90deg, #ffffff, #d1d5db, #6b7280, #111111, #d1d5db, #ffffff)",
                  backgroundSize: "400% 100%",
                  animation: "rainbow-border 20s linear infinite",
                  borderRadius: "9999px",
                }}
              />
              <div
                className="absolute inset-0 rounded-full blur-md opacity-75"
                style={{
                  background: "linear-gradient(90deg, #ffffff, #d1d5db, #6b7280, #111111, #d1d5db, #ffffff)",
                  backgroundSize: "400% 100%",
                  animation: "rainbow-border 20s linear infinite",
                  borderRadius: "9999px",
                }}
              />
              <div className="relative bg-black rounded-full">
                <PlaceholdersAndVanishInput placeholders={placeholders} onChange={handleChange} onSubmit={onSubmit} />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12 w-full max-w-3xl">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center">
                <Share2 className="w-4 h-4 text-white" />
              </div>
              <GradientButton width="160px" height="36px" onClick={() => onNavigate(2)}>
                <span className="text-white font-medium text-sm">图谱知识网络</span>
              </GradientButton>
              <p className="text-white text-xs leading-relaxed opacity-80 max-w-48">
                8800+医疗建筑实体，21000+关联，全景知识图谱可视洞察
              </p>
            </div>

            <div className="flex flex-col items-center gap-3 text-center">
              <div className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center">
                <Bot className="w-4 h-4 text-white" />
              </div>
              <GradientButton width="140px" height="36px" onClick={handleChatClick}>
                <span className="text-white font-medium text-sm">智能设计助手</span>
              </GradientButton>
              <p className="text-white text-xs leading-relaxed opacity-80 max-w-48">
                多智能体协同分析，即时响应复杂设计咨询与方案建议
              </p>
            </div>

            <div className="flex flex-col items-center gap-3 text-center">
              <div className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center">
                <Layers className="w-4 h-4 text-white" />
              </div>
              <GradientButton width="160px" height="36px" onClick={() => onNavigate(1)}>
                <span className="text-white font-medium text-sm">多源知识检索</span>
              </GradientButton>
              <p className="text-white text-xs leading-relaxed opacity-80 max-w-48">
                标准规范、学术论文、案例、政策文件一体化智能检索
              </p>
            </div>
          </div>
        </div>
      </main>

      <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 flex flex-col items-center gap-2 relative z-10">
        <button
          onClick={() => onNavigate(1)}
          data-nav-button
          className="text-white/60 hover:text-white transition-colors animate-bounce"
        >
          <ChevronDown className="w-6 h-6" />
        </button>
      </div>
    </section>
  )
}
