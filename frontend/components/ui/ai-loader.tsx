"use client"

import { useState, useEffect } from "react"

interface AILoaderProps {
  color?: "blue" | "green" | "gray"
  active?: boolean
}

export function AILoader({ color = "blue", active = true }: AILoaderProps) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted || !active) return null

  const colorMap = {
    blue: {
      light: "#38bdf8",
      mid: "#0ea5e9",
      dark: "#0284c7",
      glow1: "rgba(56, 189, 248, 0.5)",
      glow2: "rgba(14, 165, 233, 0.35)",
      glow3: "rgba(2, 132, 199, 0.25)",
    },
    green: {
      light: "#4ade80",
      mid: "#22c55e",
      dark: "#16a34a",
      glow1: "rgba(74, 222, 128, 0.5)",
      glow2: "rgba(34, 197, 94, 0.35)",
      glow3: "rgba(22, 163, 74, 0.25)",
    },
    gray: {
      light: "#9ca3af",
      mid: "#6b7280",
      dark: "#4b5563",
      glow1: "rgba(156, 163, 175, 0.4)",
      glow2: "rgba(107, 114, 128, 0.3)",
      glow3: "rgba(75, 85, 99, 0.2)",
    },
  }

  const colors = colorMap[color]

  return (
    <div className="absolute inset-0 rounded-lg overflow-hidden pointer-events-none">
      <div
        className="absolute inset-0 rounded-lg"
        style={{
          animation: "loaderRotate 5s linear infinite",
        }}
      />
      <div
        className="absolute inset-0 rounded-lg"
        style={{
          background: `radial-gradient(ellipse 80% 60% at 50% 40%, ${colors.glow1} 0%, ${colors.glow2} 35%, transparent 60%)`,
          opacity: 0.3,
        }}
      />
      <div
        className="absolute inset-0 rounded-lg"
        style={{
          boxShadow: `inset 0 0 25px ${colors.glow1}, inset 0 0 15px ${colors.glow2}`,
          animation: "pulseGlow 2.5s ease-in-out infinite",
        }}
      />
      <style jsx>{`
        @keyframes loaderRotate {
          0% {
            transform: rotate(90deg);
            box-shadow:
              0 6px 14px 0 ${colors.light} inset,
              0 14px 20px 0 ${colors.mid} inset,
              0 38px 38px 0 ${colors.dark} inset,
              0 0 4px 1.8px ${colors.glow1},
              0 0 10px 2.5px ${colors.glow2},
              0 0 20px 3.5px ${colors.glow3};
          }
          50% {
            transform: rotate(270deg);
            box-shadow:
              0 6px 14px 0 ${colors.mid} inset,
              0 14px 8px 0 ${colors.light} inset,
              0 26px 38px 0 ${colors.dark} inset,
              0 0 4px 1.8px ${colors.glow2},
              0 0 10px 2.5px ${colors.glow1},
              0 0 20px 3.5px ${colors.glow3};
          }
          100% {
            transform: rotate(450deg);
            box-shadow:
              0 6px 14px 0 ${colors.light} inset,
              0 14px 20px 0 ${colors.mid} inset,
              0 38px 38px 0 ${colors.dark} inset,
              0 0 4px 1.8px ${colors.glow1},
              0 0 10px 2.5px ${colors.glow2},
              0 0 20px 3.5px ${colors.glow3};
          }
        }
        @keyframes pulseGlow {
          0%,
          100% {
            opacity: 0.25;
          }
          50% {
            opacity: 0.45;
          }
        }
      `}</style>
    </div>
  )
}
