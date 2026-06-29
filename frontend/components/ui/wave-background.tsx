"use client"
import type * as React from "react"
import { useEffect, useRef } from "react"
import { createNoise2D } from "simplex-noise"

const DEFAULT_MAX_FPS = 30
const FRAME_INTERVAL_MS = 1000 / DEFAULT_MAX_FPS
const BASE_LINE_GAP = 10
const BASE_POINT_GAP = 14
const WAVE_TIME_SCALE = 1.45

interface Point {
  x: number
  y: number
  wave: { x: number; y: number }
  cursor: {
    x: number
    y: number
    vx: number
    vy: number
  }
}

interface WavesProps {
  className?: string
  strokeColor?: string
  backgroundColor?: string
  pointerSize?: number
  paused?: boolean
}

export function Waves({
  className = "",
  strokeColor = "#ffffff", // White lines
  backgroundColor = "#000000", // Black background
  pointerSize = 0.5,
  paused = false,
}: WavesProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const pausedRef = useRef(paused)
  pausedRef.current = paused
  const mouseRef = useRef({
    x: -10,
    y: 0,
    lx: 0,
    ly: 0,
    sx: 0,
    sy: 0,
    v: 0,
    vs: 0,
    a: 0,
    set: false,
  })
  const pathsRef = useRef<SVGPathElement[]>([])
  const linesRef = useRef<Point[][]>([])
  const noiseRef = useRef<((x: number, y: number) => number) | null>(null)
  const rafRef = useRef<number | null>(null)
  const tickRef = useRef<(time: number) => void>(() => {})
  const boundingRef = useRef<DOMRect | null>(null)
  const isVisibleRef = useRef(true)
  const isDocumentVisibleRef = useRef(true)
  const lastFrameTimeRef = useRef(0)

  // Initialization
  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return

    // Initialize noise generator
    noiseRef.current = createNoise2D()

    // Initialize size and lines
    setSize()
    setLines()

    if (containerRef.current) {
      (containerRef.current as any).stopAnimation = () => {
        if (rafRef.current !== null) {
          cancelAnimationFrame(rafRef.current)
          rafRef.current = null
        }
        // Clear paths for faster cleanup
        pathsRef.current.forEach((path) => {
          path.setAttribute("d", "")
        })
      }
    }

    // Bind events
    window.addEventListener("resize", onResize)
    window.addEventListener("mousemove", onMouseMove, { passive: true })
    document.addEventListener("visibilitychange", onVisibilityChange)
    containerRef.current.addEventListener("touchmove", onTouchMove, { passive: true })

    const observer = new IntersectionObserver(([entry]) => {
      isVisibleRef.current = entry.isIntersecting
      if (shouldAnimate()) {
        startAnimation()
      } else {
        stopAnimation()
      }
    })
    observer.observe(containerRef.current)

    // Start animation
    startAnimation()

    return () => {
      stopAnimation()
      observer.disconnect()
      window.removeEventListener("resize", onResize)
      window.removeEventListener("mousemove", onMouseMove)
      document.removeEventListener("visibilitychange", onVisibilityChange)
      containerRef.current?.removeEventListener("touchmove", onTouchMove)
    }
  }, [])

  const shouldAnimate = () => !pausedRef.current && isVisibleRef.current && isDocumentVisibleRef.current

  const startAnimation = () => {
    if (!shouldAnimate() || rafRef.current !== null) return

    lastFrameTimeRef.current = 0
    rafRef.current = requestAnimationFrame((time) => tickRef.current(time))
  }

  const stopAnimation = () => {
    if (rafRef.current === null) return

    cancelAnimationFrame(rafRef.current)
    rafRef.current = null
  }

  // Set SVG size
  const setSize = () => {
    if (!containerRef.current || !svgRef.current) return

    boundingRef.current = containerRef.current.getBoundingClientRect()
    const { width, height } = boundingRef.current

    svgRef.current.style.width = `${width}px`
    svgRef.current.style.height = `${height}px`
  }

  // Setup lines - more points for smoother curves
  const setLines = () => {
    if (!svgRef.current || !boundingRef.current) return

    const { width, height } = boundingRef.current
    linesRef.current = []

    // Clear existing paths
    pathsRef.current.forEach((path) => {
      path.remove()
    })
    pathsRef.current = []

    const densityScale = width < 768 ? 1.25 : 1
    const xGap = BASE_LINE_GAP * densityScale
    const yGap = BASE_POINT_GAP * densityScale

    const oWidth = width + 120
    const oHeight = height + 30

    const totalLines = Math.ceil(oWidth / xGap)
    const totalPoints = Math.ceil(oHeight / yGap)

    const xStart = (width - xGap * totalLines) / 2
    const yStart = (height - yGap * totalPoints) / 2

    // Create vertical lines
    for (let i = 0; i < totalLines; i++) {
      const points: Point[] = []

      for (let j = 0; j < totalPoints; j++) {
        const point: Point = {
          x: xStart + xGap * i,
          y: yStart + yGap * j,
          wave: { x: 0, y: 0 },
          cursor: { x: 0, y: 0, vx: 0, vy: 0 },
        }

        points.push(point)
      }

      // Create SVG path
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path")
      path.classList.add("a__line")
      path.classList.add("js-line")
      path.setAttribute("fill", "none")
      path.setAttribute("stroke", strokeColor)
      path.setAttribute("stroke-width", "1")
      path.setAttribute("stroke-linecap", "round")
      path.setAttribute("stroke-linejoin", "round")

      svgRef.current.appendChild(path)
      pathsRef.current.push(path)

      // Add points
      linesRef.current.push(points)
    }
  }

  // Resize handler
  const onResize = () => {
    setSize()
    setLines()
  }

  // Mouse handler
  const onMouseMove = (e: MouseEvent) => {
    updateMousePosition(e.pageX, e.pageY)
  }

  // Touch handler
  const onTouchMove = (e: TouchEvent) => {
    const touch = e.touches[0]
    updateMousePosition(touch.clientX, touch.clientY)
  }

  const onVisibilityChange = () => {
    isDocumentVisibleRef.current = document.visibilityState === "visible"
    if (shouldAnimate()) {
      startAnimation()
    } else {
      stopAnimation()
    }
  }

  // Update mouse position
  const updateMousePosition = (x: number, y: number) => {
    if (!boundingRef.current) return

    const mouse = mouseRef.current
    mouse.x = x - boundingRef.current.left
    mouse.y = y - boundingRef.current.top + window.scrollY

    if (!mouse.set) {
      mouse.sx = mouse.x
      mouse.sy = mouse.y
      mouse.lx = mouse.x
      mouse.ly = mouse.y

      mouse.set = true
    }

    // Update CSS variables
    if (containerRef.current) {
      containerRef.current.style.setProperty("--x", `${mouse.sx}px`)
      containerRef.current.style.setProperty("--y", `${mouse.sy}px`)
    }
  }

  // Move points - smoother wave motion
  const movePoints = (time: number) => {
    const { current: lines } = linesRef
    const { current: mouse } = mouseRef
    const { current: noise } = noiseRef

    if (!noise) return

    lines.forEach((points) => {
      points.forEach((p: Point) => {
        // Wave movement - reduced amplitude for smoother waves
        const move =
          noise(
            (p.x + time * 0.008 * WAVE_TIME_SCALE) * 0.003,
            (p.y + time * 0.003 * WAVE_TIME_SCALE) * 0.002,
          ) * 8 // Reduced amplitude for smoother waves

        p.wave.x = Math.cos(move) * 12 // Reduced horizontal amplitude
        p.wave.y = Math.sin(move) * 6 // Reduced vertical amplitude

        // Mouse effect - smoother response
        const dx = p.x - mouse.sx
        const dy = p.y - mouse.sy
        const d = Math.hypot(dx, dy)
        const l = Math.max(175, mouse.vs)

        if (d < l) {
          const s = 1 - d / l
          const f = Math.cos(d * 0.001) * s

          p.cursor.vx += Math.cos(mouse.a) * f * l * mouse.vs * 0.00035 // Reduced influence
          p.cursor.vy += Math.sin(mouse.a) * f * l * mouse.vs * 0.00035 // Reduced influence
        }

        p.cursor.vx += (0 - p.cursor.x) * 0.01 // Increased restoration force
        p.cursor.vy += (0 - p.cursor.y) * 0.01 // Increased restoration force

        p.cursor.vx *= 0.95 // Increased smoothness
        p.cursor.vy *= 0.95 // Increased smoothness

        p.cursor.x += p.cursor.vx
        p.cursor.y += p.cursor.vy

        p.cursor.x = Math.min(50, Math.max(-50, p.cursor.x)) // Limited deformation range
        p.cursor.y = Math.min(50, Math.max(-50, p.cursor.y)) // Limited deformation range
      })
    })
  }

  // Get moved point coordinates
  const moved = (point: Point, withCursorForce = true) => {
    const coords = {
      x: point.x + point.wave.x + (withCursorForce ? point.cursor.x : 0),
      y: point.y + point.wave.y + (withCursorForce ? point.cursor.y : 0),
    }

    return coords
  }

  const buildSmoothPath = (points: Point[]) => {
    const firstPoint = moved(points[0], false)
    let d = `M ${firstPoint.x} ${firstPoint.y}`

    for (let i = 1; i < points.length - 1; i++) {
      const current = moved(points[i])
      const next = moved(points[i + 1])
      const midX = (current.x + next.x) / 2
      const midY = (current.y + next.y) / 2

      d += ` Q ${current.x} ${current.y} ${midX} ${midY}`
    }

    const lastPoint = moved(points[points.length - 1])
    return `${d} L ${lastPoint.x} ${lastPoint.y}`
  }

  // Draw lines - using smoothed curves
  const drawLines = () => {
    const { current: lines } = linesRef
    const { current: paths } = pathsRef

    lines.forEach((points, lIndex) => {
      if (points.length < 2 || !paths[lIndex]) return

      paths[lIndex].setAttribute("d", buildSmoothPath(points))
    })
  }

  // Animation logic
  const tick = (time: number) => {
    if (!shouldAnimate()) {
      rafRef.current = null
      return
    }

    const elapsed = time - lastFrameTimeRef.current
    if (lastFrameTimeRef.current !== 0 && elapsed < FRAME_INTERVAL_MS) {
      rafRef.current = requestAnimationFrame((nextTime) => tickRef.current(nextTime))
      return
    }
    lastFrameTimeRef.current = time - (elapsed % FRAME_INTERVAL_MS)

    const { current: mouse } = mouseRef

    // Smooth mouse movement
    mouse.sx += (mouse.x - mouse.sx) * 0.1
    mouse.sy += (mouse.y - mouse.sy) * 0.1

    // Mouse velocity
    const dx = mouse.x - mouse.lx
    const dy = mouse.y - mouse.ly
    const d = Math.hypot(dx, dy)

    mouse.v = d
    mouse.vs += (d - mouse.vs) * 0.1
    mouse.vs = Math.min(100, mouse.vs)

    // Previous mouse position
    mouse.lx = mouse.x
    mouse.ly = mouse.y

    // Mouse angle
    mouse.a = Math.atan2(dy, dx)

    // Animation
    if (containerRef.current) {
      containerRef.current.style.setProperty("--x", `${mouse.sx}px`)
      containerRef.current.style.setProperty("--y", `${mouse.sy}px`)
    }

    movePoints(time)
    drawLines()

    rafRef.current = requestAnimationFrame((nextTime) => tickRef.current(nextTime))
  }

  useEffect(() => {
    if (shouldAnimate()) {
      startAnimation()
    } else {
      stopAnimation()
    }
  }, [paused])

  tickRef.current = tick

  return (
    <div
      ref={containerRef}
      className={`waves-component relative overflow-hidden ${className}`}
      style={
        {
          backgroundColor,
          position: "absolute",
          top: 0,
          left: 0,
          margin: 0,
          padding: 0,
          width: "100%",
          height: "100%",
          overflow: "hidden",
          "--x": "-0.5rem",
          "--y": "50%",
        } as React.CSSProperties
      }
    >
      <svg ref={svgRef} className="block w-full h-full js-svg" xmlns="http://www.w3.org/2000/svg" />
      <div
        className="pointer-dot"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: `${pointerSize}rem`,
          height: `${pointerSize}rem`,
          background: strokeColor,
          borderRadius: "50%",
          transform: "translate3d(calc(var(--x) - 50%), calc(var(--y) - 50%), 0)",
          willChange: "transform",
        }}
      />
    </div>
  )
}
