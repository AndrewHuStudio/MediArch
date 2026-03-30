"use client"

import { motion } from "framer-motion"
import { Lightbulb } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/lib/i18n"

interface SuggestedQuestionsProps {
  questions: string[]
  onQuestionClick: (question: string) => void
  className?: string
  maxQuestions?: number
}

export function SuggestedQuestions({
  questions,
  onQuestionClick,
  className,
  maxQuestions = 4
}: SuggestedQuestionsProps) {
  // 限制显示的问题数量，默认最多4个
  const displayQuestions = questions.slice(0, maxQuestions)
  const { t } = useT()

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.2 }}
      className={cn("w-full", className)}
    >
      <div className="flex items-center gap-2 mb-2">
        <Lightbulb className="w-3.5 h-3.5 text-blue-400" />
        <span className="text-xs text-gray-400 font-medium">{t('chat.suggestedQuestions')}</span>
      </div>

      {/* 使用 2x2 网格布局，更紧凑 */}
      <div className="grid grid-cols-2 gap-2">
        {displayQuestions.map((question, index) => (
          <motion.button
            key={index}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.2, delay: index * 0.03 }}
            onClick={() => onQuestionClick(question)}
            className={cn(
              "group relative px-3 py-1.5 rounded-md text-xs text-gray-300 text-left",
              "bg-white/5 border border-white/15",
              "hover:border-blue-500/50 hover:text-white hover:bg-white/10",
              "transition-all duration-200",
              "hover:shadow-[0_0_10px_rgba(59,130,246,0.2)]",
              "active:scale-[0.98]",
              "truncate",
            )}
            title={question}
          >
            <span className="relative z-10 line-clamp-1">{question}</span>
            <div
              className={cn(
                "absolute inset-0 rounded-md opacity-0 group-hover:opacity-100",
                "bg-gradient-to-r from-blue-500/10 to-transparent",
                "transition-opacity duration-200",
              )}
            />
          </motion.button>
        ))}
      </div>
    </motion.div>
  )
}
