"use client"

import { useEffect, useRef } from "react"
import { cn } from "@/lib/utils"

interface BorderLoaderProps {
  active?: boolean
  radius?: number
  strokeWidth?: number
  speed?: number
  color?: "blue" | "green"
  className?: string
}

export function BorderLoader({
  active = true,
  radius = 12,
  strokeWidth = 8,
  speed = 3,
  color = "blue",
  className,
}: BorderLoaderProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const gradientId = useRef(`gradient-${Math.random().toString(36).substr(2, 9)}`)
  const glowId = useRef(`glow-${Math.random().toString(36).substr(2, 9)}`)

  useEffect(() => {
    if (!active || !svgRef.current) return

    const rect = svgRef.current.querySelector("rect")
    if (rect) {
      const length = rect.getTotalLength()
      rect.style.setProperty("--path-length", length.toString())
    }
  }, [active])

  if (!active) return null

  const colors = {
    blue: {
      bright: "rgba(59, 130, 246, 1)",
      mid: "rgba(59, 130, 246, 0.6)",
      dim: "rgba(59, 130, 246, 0.3)",
      transparent: "rgba(59, 130, 246, 0)",
    },
    green: {
      bright: "rgba(34, 197, 94, 1)",
      mid: "rgba(34, 197, 94, 0.6)",
      dim: "rgba(34, 197, 94, 0.3)",
      transparent: "rgba(34, 197, 94, 0)",
    },
  }

  const currentColors = colors[color]

  return (
    <>
      <svg
        ref={svgRef}
        className={cn("absolute inset-0 pointer-events-none", className)}
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        style={{
          width: "100%",
          height: "100%",
        }}
      >
        <defs>
          <linearGradient id={`${gradientId.current}-outer`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={currentColors.transparent} />
            <stop offset="30%" stopColor={currentColors.dim} />
            <stop offset="50%" stopColor={currentColors.bright} />
            <stop offset="70%" stopColor={currentColors.dim} />
            <stop offset="100%" stopColor={currentColors.transparent} />
          </linearGradient>

          <linearGradient id={`${gradientId.current}-mid`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={currentColors.transparent} />
            <stop offset="35%" stopColor={currentColors.mid} />
            <stop offset="50%" stopColor={currentColors.bright} />
            <stop offset="65%" stopColor={currentColors.mid} />
            <stop offset="100%" stopColor={currentColors.transparent} />
          </linearGradient>

          <linearGradient id={`${gradientId.current}-inner`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={currentColors.transparent} />
            <stop offset="40%" stopColor={currentColors.dim} />
            <stop offset="50%" stopColor={currentColors.mid} />
            <stop offset="60%" stopColor={currentColors.dim} />
            <stop offset="100%" stopColor={currentColors.transparent} />
          </linearGradient>

          <filter id={`${glowId.current}-heavy`}>
            <feGaussianBlur in="SourceGraphic" stdDeviation="4" />
          </filter>
          <filter id={`${glowId.current}-medium`}>
            <feGaussianBlur in="SourceGraphic" stdDeviation="2.5" />
          </filter>
          <filter id={`${glowId.current}-light`}>
            <feGaussianBlur in="SourceGraphic" stdDeviation="1.5" />
          </filter>
        </defs>

        <rect
          x={strokeWidth / 2}
          y={strokeWidth / 2}
          width={100 - strokeWidth}
          height={100 - strokeWidth}
          rx={(radius / 100) * 100}
          ry={(radius / 100) * 100}
          fill="none"
          stroke={`url(#${gradientId.current}-outer)`}
          strokeWidth={strokeWidth * 2}
          strokeLinecap="round"
          pathLength="1"
          filter={`url(#${glowId.current}-heavy)`}
          style={{
            strokeDasharray: "0.2 0.8",
            animation: `borderRun ${speed}s linear infinite`,
            opacity: 0.6,
          }}
        />

        <rect
          x={strokeWidth / 2}
          y={strokeWidth / 2}
          width={100 - strokeWidth}
          height={100 - strokeWidth}
          rx={(radius / 100) * 100}
          ry={(radius / 100) * 100}
          fill="none"
          stroke={`url(#${gradientId.current}-mid)`}
          strokeWidth={strokeWidth * 1.5}
          strokeLinecap="round"
          pathLength="1"
          filter={`url(#${glowId.current}-medium)`}
          style={{
            strokeDasharray: "0.2 0.8",
            animation: `borderRun ${speed}s linear infinite`,
            opacity: 0.8,
          }}
        />

        <rect
          x={strokeWidth / 2}
          y={strokeWidth / 2}
          width={100 - strokeWidth}
          height={100 - strokeWidth}
          rx={(radius / 100) * 100}
          ry={(radius / 100) * 100}
          fill="none"
          stroke={`url(#${gradientId.current}-inner)`}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          pathLength="1"
          filter={`url(#${glowId.current}-light)`}
          style={{
            strokeDasharray: "0.18 0.82",
            animation: `borderRun ${speed}s linear infinite`,
            opacity: 1,
          }}
        />
      </svg>
      <style jsx>{`
        @keyframes borderRun {
          from {
            stroke-dashoffset: 1;
          }
          to {
            stroke-dashoffset: 0;
          }
        }
      `}</style>
    </>
  )
}
