"use client"

import type React from "react"

import { motion } from "framer-motion"
import { cn } from "@/lib/utils"

interface RotatingBorderCardProps {
  children: React.ReactNode
  isActive?: boolean
  isComplete?: boolean
  className?: string
}

export function RotatingBorderCard({
  children,
  isActive = false,
  isComplete = false,
  className,
}: RotatingBorderCardProps) {
  return (
    <div className={cn("relative", className)}>
      {isActive && !isComplete && (
        <motion.div
          className="absolute inset-0 rounded-lg"
          style={{
            background: "conic-gradient(from 0deg, transparent 0%, rgba(59, 130, 246, 0.8) 10%, transparent 20%)",
            padding: "2px",
          }}
          animate={{ rotate: 360 }}
          transition={{
            duration: 3,
            repeat: Number.POSITIVE_INFINITY,
            ease: "linear",
          }}
        >
          <div className="w-full h-full bg-transparent rounded-lg" />
        </motion.div>
      )}

      {isComplete && <div className="absolute inset-0 rounded-lg border-2 border-green-500" />}

      <div
        className={cn(
          "relative z-10 rounded-lg p-3",
          isActive && !isComplete && "bg-blue-500/20 border border-blue-500/30",
          isComplete && "bg-green-500/20 border border-green-500/30",
          !isActive && !isComplete && "bg-white/5 border border-white/10",
        )}
      >
        {children}
      </div>
    </div>
  )
}
