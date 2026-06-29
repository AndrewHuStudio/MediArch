"use client"
import { cn } from "@/lib/utils"
import { useEffect, useRef, useState } from "react"
import { createNoise3D } from "simplex-noise"

const DEFAULT_MAX_FPS = 30
const FRAME_INTERVAL_MS = 1000 / DEFAULT_MAX_FPS

export const WavyBackground = ({
  children,
  className,
  containerClassName,
  colors,
  waveWidth,
  backgroundFill,
  blur = 10,
  speed = "fast",
  waveOpacity = 0.5,
  ...props
}: {
  children?: any
  className?: string
  containerClassName?: string
  colors?: string[]
  waveWidth?: number
  backgroundFill?: string
  blur?: number
  speed?: "slow" | "fast"
  waveOpacity?: number
  [key: string]: any
}) => {
  const noise = createNoise3D()
  let w: number, h: number, nt: number, i: number, x: number, ctx: any, canvas: any
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const animationIdRef = useRef<number | null>(null)
  const lastFrameTimeRef = useRef(0)
  const isVisibleRef = useRef(true)
  const isDocumentVisibleRef = useRef(true)

  const getSpeed = () => {
    switch (speed) {
      case "slow":
        return 0.003
      case "fast":
        return 0.009
      default:
        return 0.003
    }
  }

  const shouldAnimate = () => isVisibleRef.current && isDocumentVisibleRef.current

  const init = () => {
    canvas = canvasRef.current
    ctx = canvas.getContext("2d")
    w = ctx.canvas.width = window.innerWidth
    h = ctx.canvas.height = window.innerHeight
    ctx.filter = `blur(${blur}px)`
    nt = 0
    window.onresize = () => {
      w = ctx.canvas.width = window.innerWidth
      h = ctx.canvas.height = window.innerHeight
      ctx.filter = `blur(${blur}px)`
    }
    startAnimation()
  }

  const waveColors = colors ?? ["#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569"]

  const drawWave = (n: number) => {
    nt += getSpeed()
    for (i = 0; i < n; i++) {
      ctx.beginPath()
      ctx.lineWidth = waveWidth || 50
      ctx.strokeStyle = waveColors[i % waveColors.length]
      for (x = 0; x < w; x += 5) {
        var y = noise(x / 800, 0.3 * i, nt) * 100
        ctx.lineTo(x, y + h * 0.5)
      }
      ctx.stroke()
      ctx.closePath()
    }
  }

  const render = (time: number) => {
    if (!shouldAnimate()) {
      animationIdRef.current = null
      return
    }

    const elapsed = time - lastFrameTimeRef.current
    if (lastFrameTimeRef.current !== 0 && elapsed < FRAME_INTERVAL_MS) {
      animationIdRef.current = requestAnimationFrame(render)
      return
    }
    lastFrameTimeRef.current = time - (elapsed % FRAME_INTERVAL_MS)

    ctx.fillStyle = backgroundFill || "#f7fbfc"
    ctx.globalAlpha = waveOpacity || 0.5
    ctx.fillRect(0, 0, w, h)
    drawWave(5)
    animationIdRef.current = requestAnimationFrame(render)
  }

  const startAnimation = () => {
    if (!shouldAnimate() || animationIdRef.current !== null) return

    lastFrameTimeRef.current = 0
    animationIdRef.current = requestAnimationFrame(render)
  }

  const stopAnimation = () => {
    if (animationIdRef.current === null) return

    cancelAnimationFrame(animationIdRef.current)
    animationIdRef.current = null
  }

  useEffect(() => {
    init()

    const observer = new IntersectionObserver(([entry]) => {
      isVisibleRef.current = entry.isIntersecting
      if (shouldAnimate()) {
        startAnimation()
      } else {
        stopAnimation()
      }
    })

    if (containerRef.current) {
      observer.observe(containerRef.current)
    }

    const handleVisibilityChange = () => {
      isDocumentVisibleRef.current = document.visibilityState === "visible"
      if (shouldAnimate()) {
        startAnimation()
      } else {
        stopAnimation()
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)

    return () => {
      stopAnimation()
      observer.disconnect()
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [])

  const [isSafari, setIsSafari] = useState(false)
  useEffect(() => {
    setIsSafari(
      typeof window !== "undefined" &&
        navigator.userAgent.includes("Safari") &&
        !navigator.userAgent.includes("Chrome"),
    )
  }, [])

  return (
    <div ref={containerRef} className={cn("h-screen flex flex-col items-center justify-center", containerClassName)}>
      <canvas
        className="absolute inset-0 z-0"
        ref={canvasRef}
        id="canvas"
        style={{
          ...(isSafari ? { filter: `blur(${blur}px)` } : {}),
        }}
      ></canvas>
      <div className={cn("relative z-10", className)} {...props}>
        {children}
      </div>
    </div>
  )
}
