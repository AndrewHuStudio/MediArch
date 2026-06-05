"use client"

import { useEffect, useRef, useMemo } from "react"
import { Brain, CheckCircle2, Circle, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { AILoader } from "@/components/ui/ai-loader"
import { useT } from "@/lib/i18n"

interface AgentThinkingPanelProps {
  activeAgentIndex: number
  agents: string[]
  agentStatus: "thinking" | "synthesizing" | "idle"
  currentThought: string
  isThinking: boolean
  // 新增：支持并行Agent状态
  activeAgents?: Set<number>
  completedAgents?: Set<number>
}

export default function AgentThinkingPanel({
  activeAgentIndex,
  agents,
  agentStatus,
  currentThought,
  isThinking,
  activeAgents = new Set(),
  completedAgents = new Set(),
}: AgentThinkingPanelProps) {
  const agentRefs = useRef<(HTMLDivElement | null)[]>([])
  const { t } = useT()

  // 当前运行的智能体列表（支持并行）
  const runningAgents = useMemo(() => {
    const running = new Set<string>()
    if (isThinking) {
      // 使用新的并行状态
      activeAgents.forEach(index => {
        if (index >= 0 && index < agents.length) {
          running.add(agents[index])
        }
      })
      // 向后兼容：如果没有并行状态，回退到单个activeAgentIndex
      if (running.size === 0 && activeAgentIndex >= 0 && activeAgentIndex < agents.length) {
        running.add(agents[activeAgentIndex])
      }
    }
    return running
  }, [isThinking, activeAgents, activeAgentIndex, agents])

  // 已完成的智能体集合
  const completedAgentNames = useMemo(() => {
    const completed = new Set<string>()
    completedAgents.forEach(index => {
      if (index >= 0 && index < agents.length) {
        completed.add(agents[index])
      }
    })
    return completed
  }, [completedAgents, agents])

  // 滚动到当前活跃的智能体
  useEffect(() => {
    if (activeAgentIndex >= 0 && agentRefs.current[activeAgentIndex]) {
      agentRefs.current[activeAgentIndex]?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      })
    }
  }, [activeAgentIndex])

  const getCardStyle = () => {
    switch (agentStatus) {
      case "thinking":
        return {
          bg: "bg-blue-50/90",
          border: "border-blue-300/60",
          dot: "bg-blue-400 animate-pulse",
          text: "text-blue-700",
          label: t('agent.status.thinking'),
          loaderActive: true,
          loaderColor: "blue" as const,
        }
      case "synthesizing":
        return {
          bg: "bg-emerald-50/90",
          border: "border-emerald-300/70",
          dot: "bg-green-400",
          text: "text-emerald-700",
          label: t('agent.status.synthesizing'),
          loaderActive: true,
          loaderColor: "green" as const,
        }
      case "idle":
      default:
        return {
          bg: "bg-slate-50/90",
          border: "border-[#cfe2e7]",
          dot: "bg-[#8ba4ad]",
          text: "text-[#6c858c]",
          label: t('agent.status.idle'),
          loaderActive: false,
          loaderColor: "blue" as const,
        }
    }
  }

  const cardStyle = getCardStyle()

  // 获取智能体的状态图标和样式
  const getAgentStyle = (agent: string, index: number) => {
    const isActive = runningAgents.has(agent)
    const isComplete = completedAgentNames.has(agent)

    if (isActive && isThinking) {
      return {
        className: "border-blue-300/70 bg-blue-50 text-blue-700 shadow-[0_0_20px_rgba(59,130,246,0.14)]",
        icon: <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin" />,
        style: {
          boxShadow: "inset 0 0 0 2px hsla(210,100%,60%,.5)",
          background: "linear-gradient(180deg, rgba(224,242,254,.92), rgba(255,255,255,.4))",
        } as React.CSSProperties,
      }
    } else if (isComplete) {
      return {
        className: "border-green-300/60 bg-green-50 text-green-700",
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />,
        style: undefined,
      }
    } else {
      return {
        className: "border-[#d9e7eb] bg-white/70 text-[#6c858c]",
        icon: <Circle className="w-3.5 h-3.5 text-[#8ba4ad]" />,
        style: undefined,
      }
    }
  }

  return (
    <div className="box-border flex h-full min-h-0 flex-col rounded-lg border border-[#cfe2e7] bg-white/72 p-4 backdrop-blur-md shadow-[0_18px_48px_rgba(15,78,99,0.1)]">
      <div className="mb-4 flex flex-shrink-0 items-center gap-2">
        <Brain className="h-5 w-5 text-blue-400" />
        <h3 className="text-sm font-semibold text-[#12323a]">{t('agent.title')}</h3>
      </div>

      <div className="flex flex-1 min-h-0 flex-col gap-3">
        <div className={cn("relative rounded-lg border-2 p-4 transition-all duration-500", cardStyle.bg, cardStyle.border)}>
          <AILoader active={cardStyle.loaderActive} color={cardStyle.loaderColor} />
          <div className="relative z-10 mb-2 flex items-center gap-2">
            <span className={`text-xs font-medium ${cardStyle.text}`}>{cardStyle.label}</span>
            <span className={`h-2 w-2 rounded-full ${cardStyle.dot}`} />
          </div>
          <div className="relative z-10 text-sm text-[#335158]">
            {isThinking ? currentThought : t('agent.waitingNext')}
          </div>
        </div>

        <div className="flex-1 space-y-2 overflow-hidden">
          <p className="text-xs text-[#6c858c]">{t('agent.progress')}</p>
          <div className="custom-scrollbar flex-1 space-y-2 overflow-y-auto pr-1">
            {agents.map((agent, index) => {
              const style = getAgentStyle(agent, index)
              return (
                <div
                  key={agent}
                  ref={(el) => { agentRefs.current[index] = el }}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-xs font-medium transition-all duration-300 flex items-center justify-between",
                    style.className,
                  )}
                  style={style.style}
                >
                  <span>{agent}</span>
                  {style.icon}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
