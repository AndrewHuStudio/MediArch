"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { ChevronRight, ChevronLeft, ChevronUp, ChevronDown } from "lucide-react"
import { useRouter } from "next/navigation"
import type * as THREE from "three"
import type { BookParams, MaterialProps } from "./types"
import { BookModel } from "./book-canvas"
import { BookDetails } from "./book-details"
import { booksData } from "./book-data"
import { WavyBackground } from "@/components/ui/wavy-background"

interface BookShowcaseProps {
  onNavigate: (sectionIndex: number) => void
}

export default function BookShowcase({ onNavigate }: BookShowcaseProps) {
  const router = useRouter()
  const [currentBookIndex, setCurrentBookIndex] = useState(0)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const [bookVisible, setBookVisible] = useState(true) // Set to true immediately on mount
  const [textVisible, setTextVisible] = useState(true) // Set to true immediately on mount
  const [isFlipping, setIsFlipping] = useState(false)
  const [hasInitialAnimationPlayed, setHasInitialAnimationPlayed] = useState(false)
  const [backgroundBookIndex, setBackgroundBookIndex] = useState(0)
  const [initialBackgroundSet, setInitialBackgroundSet] = useState(false)
  const [bookOpacity, setBookOpacity] = useState(1)
  const [textColor, setTextColor] = useState("white")
  const [hasRenderedWithTextures, setHasRenderedWithTextures] = useState(false)
  const [highlightIntensity, setHighlightIntensity] = useState(0)
  const [highlightTrigger, setHighlightTrigger] = useState(0)
  const currentBook = booksData[currentBookIndex]
  const showcaseRef = useRef<HTMLDivElement | null>(null)

  const getWavyBackgroundConfig = (genres: string[]) => {
    if (genres.includes("规范标准")) {
      return {
        colors: ["#f1f5f9", "#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b"],
        backgroundFill: "#000000",
        waveOpacity: 0.4,
        blur: 8,
        speed: "fast" as const,
      }
    }
    if (genres.includes("书籍报告")) {
      return {
        colors: ["#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569"],
        backgroundFill: "#000000",
        waveOpacity: 0.4,
        blur: 9,
        speed: "fast" as const,
      }
    }
    if (genres.includes("参考论文")) {
      return {
        colors: ["#cbd5e1", "#94a3b8", "#64748b", "#475569", "#334155"],
        backgroundFill: "#000000",
        waveOpacity: 0.5,
        blur: 10,
        speed: "fast" as const,
      }
    }
    if (genres.includes("政策文件")) {
      return {
        colors: ["#94a3b8", "#64748b", "#475569", "#334155", "#1e293b"],
        backgroundFill: "#000000",
        waveOpacity: 0.5,
        blur: 9,
        speed: "fast" as const,
      }
    }
    if (genres.includes("在线案例")) {
      return {
        colors: ["#64748b", "#475569", "#334155", "#1e293b", "#0f172a"],
        backgroundFill: "#000000",
        waveOpacity: 0.6,
        blur: 8,
        speed: "fast" as const,
      }
    }
    // Default fallback
    return {
      colors: ["#f1f5f9", "#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b"],
      backgroundFill: "#000000",
      waveOpacity: 0.4,
      blur: 8,
      speed: "fast" as const,
    }
  }

  const getResponsiveFOV = (width: number, height: number) => {
    const aspectRatio = width / height
    const viewportArea = width * height

    // Mobile/portrait orientation
    if (width < 1024) {
      return aspectRatio < 1 ? 26 : 28
    }

    // Medium screens - consider both size and aspect ratio
    if (width <= 1200) {
      if (aspectRatio < 1.3) return 48
      return 30
    }

    // Large screens - adjust based on total viewport area
    if (viewportArea > 2500000) {
      return aspectRatio > 2 ? 42 : 38
    }

    // Standard large screens
    return aspectRatio < 1.3 ? 44 : 30
  }

  const [screenWidth, setScreenWidth] = useState(typeof window !== "undefined" ? window.innerWidth : 1200)
  const [screenHeight, setScreenHeight] = useState(typeof window !== "undefined" ? window.innerHeight : 800)
  const [bookReady, setBookReady] = useState(false)
  const [fovReady, setFovReady] = useState(false)

  const initialParams: BookParams = {
    scale: [5, 5, 5],
    position: [-3, -3, -3],
    rotation: [1.6, 0.0, 0.3],
    cameraPosition: [-4.1, -3.3, 0.2],
    cameraFov: getResponsiveFOV(screenWidth, screenHeight),
  }

  const defaultParams: BookParams = {
    scale: [5, 5, 5],
    position: [-3, -3, -3],
    rotation: [1.2, 0, 0],
    cameraPosition: [-4.2, -2.9, 0.4],
    cameraFov: getResponsiveFOV(screenWidth, screenHeight),
  }

  const defaultMaterialProps: MaterialProps = currentBook.materialProps

  const [params, setParams] = useState<BookParams>(initialParams)
  const [materialProps, setMaterialProps] = useState<MaterialProps>(defaultMaterialProps)
  const object2MeshRef = useRef<THREE.Mesh | null>(null)

  const triggerHighlight = useCallback(() => {
    setHighlightTrigger((prev) => prev + 1)
  }, [])

  const jumpToOnlineCases = () => {
    if (isTransitioning) return

    const onlineCasesIndex = booksData.findIndex((book) => book.genres.includes("在线案例"))
    if (onlineCasesIndex !== -1 && onlineCasesIndex !== currentBookIndex) {
      setIsTransitioning(true)
      setIsFlipping(true)
      setTextVisible(false)
      setBookOpacity(0)

      const startTime = Date.now()
      const flipDuration = 1200
      const startRotationZ = params.rotation[2]
      let contentChanged = false
      let bookFadedIn = false

      const animateFlip = () => {
        const elapsed = Date.now() - startTime
        const progress = Math.min(elapsed / flipDuration, 1)

        const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
        const easedProgress = easeOutCubic(progress)

        const flipRotation = easedProgress * Math.PI * 2

        setParams((prev) => ({
          ...prev,
          rotation: [prev.rotation[0], prev.rotation[1], startRotationZ + flipRotation],
        }))

        if (progress >= 0.3 && !contentChanged) {
          setCurrentBookIndex(onlineCasesIndex)
          setBackgroundBookIndex(onlineCasesIndex)
          setMaterialProps(booksData[onlineCasesIndex].materialProps)
          contentChanged = true
        }

        if (progress >= 0.5 && !bookFadedIn) {
          setBookOpacity(1)
          bookFadedIn = true
        }

        if (progress >= 0.55 && contentChanged && !textVisible) {
          setTextVisible(true)
        }

        if (progress < 1) {
          requestAnimationFrame(animateFlip)
        } else {
          setIsFlipping(false)
          setTextVisible(true)
          setBookOpacity(1)
          setIsTransitioning(false)
        }
      }

      requestAnimationFrame(animateFlip)
    }
  }

  const nextBook = () => {
    if (isTransitioning) return

    setIsTransitioning(true)
    setIsFlipping(true)
    setTextVisible(false)
    setBookOpacity(0)

    const nextBookIndex = (currentBookIndex + 1) % booksData.length
    const startTime = Date.now()
    const flipDuration = 1200
    const startRotationZ = params.rotation[2]
    let contentChanged = false
    let bookFadedIn = false

    const animateFlip = () => {
      const elapsed = Date.now() - startTime
      const progress = Math.min(elapsed / flipDuration, 1)

      const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
      const easedProgress = easeOutCubic(progress)

      const flipRotation = easedProgress * Math.PI * 2

      setParams((prev) => ({
        ...prev,
        rotation: [prev.rotation[0], prev.rotation[1], startRotationZ + flipRotation],
      }))

      if (progress >= 0.3 && !contentChanged) {
        setCurrentBookIndex(nextBookIndex)
        setBackgroundBookIndex(nextBookIndex)
        setMaterialProps(booksData[nextBookIndex].materialProps)
        contentChanged = true
      }

      if (progress >= 0.5 && !bookFadedIn) {
        setBookOpacity(1)
        bookFadedIn = true
      }

      if (progress >= 0.55 && contentChanged && !textVisible) {
        setTextVisible(true)
      }

      if (progress < 1) {
        requestAnimationFrame(animateFlip)
      } else {
        setIsFlipping(false)
        setTextVisible(true)
        setBookOpacity(1)
        setIsTransitioning(false)
      }
    }

    requestAnimationFrame(animateFlip)
  }

  const previousBook = () => {
    if (isTransitioning) return

    setIsTransitioning(true)
    setIsFlipping(true)
    setTextVisible(false)
    setBookOpacity(0)

    const prevBookIndex = currentBookIndex === 0 ? booksData.length - 1 : currentBookIndex - 1
    const startTime = Date.now()
    const flipDuration = 1200
    const startRotationZ = params.rotation[2]
    let contentChanged = false
    let bookFadedIn = false

    const animateFlip = () => {
      const elapsed = Date.now() - startTime
      const progress = Math.min(elapsed / flipDuration, 1)

      const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
      const easedProgress = easeOutCubic(progress)

      const flipRotation = easedProgress * Math.PI * -2

      setParams((prev) => ({
        ...prev,
        rotation: [prev.rotation[0], prev.rotation[1], startRotationZ + flipRotation],
      }))

      if (progress >= 0.3 && !contentChanged) {
        setCurrentBookIndex(prevBookIndex)
        setBackgroundBookIndex(prevBookIndex)
        setMaterialProps(booksData[prevBookIndex].materialProps)
        contentChanged = true
      }

      if (progress >= 0.5 && !bookFadedIn) {
        setBookOpacity(1)
        bookFadedIn = true
      }

      if (progress >= 0.55 && contentChanged && !textVisible) {
        setTextVisible(true)
      }

      if (progress < 1) {
        requestAnimationFrame(animateFlip)
      } else {
        setIsFlipping(false)
        setTextVisible(true)
        setBookOpacity(1)
        setIsTransitioning(false)
      }
    }

    requestAnimationFrame(animateFlip)
  }

  useEffect(() => {
    if (highlightTrigger === 0) return
    setHighlightIntensity(1)
    let animationFrame: number
    let current = 1

    const animate = () => {
      current = Math.max(0, current - 0.02)
      setHighlightIntensity(current)
      if (current > 0.01) {
        animationFrame = requestAnimationFrame(animate)
      }
    }

    animationFrame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(animationFrame)
  }, [highlightTrigger])

  useEffect(() => {
    const animateToDefault = () => {
      const startTime = Date.now()
      const duration = 1000

      const animate = () => {
        const elapsed = Date.now() - startTime
        const progress = Math.min(elapsed / duration, 1)

        const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1)

        const easedProgress = easeInOutCubic(progress)

        const startRot = initialParams.rotation
        const endRot = defaultParams.rotation
        const currentRot: [number, number, number] = [
          startRot[0] + (endRot[0] - startRot[0]) * easedProgress,
          startRot[1] + (endRot[1] - startRot[1]) * easedProgress,
          startRot[2] + (endRot[2] - startRot[2]) * easedProgress,
        ]

        const startFOV = initialParams.cameraFov
        const endFOV = defaultParams.cameraFov
        const currentFOV = startFOV + (endFOV - startFOV) * easedProgress

        setParams((prev) => ({
          ...prev,
          rotation: currentRot,
          cameraFov: currentFOV,
        }))

        if (progress < 1) {
          requestAnimationFrame(animate)
        } else {
          setHasInitialAnimationPlayed(true)
        }
      }

      requestAnimationFrame(animate)
    }

    if (bookReady && !hasInitialAnimationPlayed && !isTransitioning) {
      animateToDefault()
    }
  }, [bookReady, hasInitialAnimationPlayed, isTransitioning])

  useEffect(() => {
    if (!bookReady) return
    triggerHighlight()
  }, [bookReady, triggerHighlight])

  useEffect(() => {
    const handleResize = () => {
      const newWidth = window.innerWidth
      const newHeight = window.innerHeight
      setScreenWidth(newWidth)
      setScreenHeight(newHeight)
      const newFOV = getResponsiveFOV(newWidth, newHeight)

      setParams((prev) => ({
        ...prev,
        cameraFov: newFOV,
      }))
    }

    window.addEventListener("resize", handleResize)
    handleResize()
    setFovReady(true)

    return () => window.removeEventListener("resize", handleResize)
  }, [])

  useEffect(() => {
    if (bookReady && fovReady) {
      // Ensure book and text are visible after a short delay
      const timer = setTimeout(() => {
        setBookVisible(true)
        setTextVisible(true)
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [bookReady, fovReady])

  useEffect(() => {
    if (!hasInitialAnimationPlayed) return
    triggerHighlight()
  }, [currentBookIndex, hasInitialAnimationPlayed, triggerHighlight])

  useEffect(() => {
    const currentBookData = booksData[backgroundBookIndex]
    const newTextColor = currentBookData.textColor

    const getGradientClass = (genres: string[]) => {
      if (genres.includes("规范标准")) return "gradient-standards"
      if (genres.includes("书籍报告")) return "gradient-reports"
      if (genres.includes("参考论文")) return "gradient-research"
      if (genres.includes("政策文件")) return "gradient-policy"
      if (genres.includes("在线案例")) return "gradient-cases"
      return "knowledge-base-gradient"
    }

    const gradientClass = getGradientClass(currentBookData.genres)

    document.body.classList.remove(
      "knowledge-base-gradient",
      "gradient-standards",
      "gradient-reports",
      "gradient-research",
      "gradient-policy",
      "gradient-cases",
    )

    document.body.classList.add(gradientClass)
    document.body.style.transition = initialBackgroundSet ? "all 0.7s ease-out" : "none"

    setTextColor(newTextColor)

    if (!initialBackgroundSet) {
      setInitialBackgroundSet(true)
    }

    return () => {
      document.body.classList.remove(
        "knowledge-base-gradient",
        "gradient-standards",
        "gradient-reports",
        "gradient-research",
        "gradient-policy",
        "gradient-cases",
      )
      document.body.style.transition = ""
    }
  }, [backgroundBookIndex, initialBackgroundSet])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") {
        nextBook()
      } else if (event.key === "ArrowLeft") {
        previousBook()
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [isTransitioning, currentBookIndex])

  useEffect(() => {
    const element = showcaseRef.current
    if (!element) return

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            triggerHighlight()
          }
        })
      },
      { threshold: 0.4 },
    )

    observer.observe(element)

    return () => observer.disconnect()
  }, [triggerHighlight])

  const wavyConfig = getWavyBackgroundConfig(currentBook.genres)

  return (
    <div ref={showcaseRef} className="h-full">
      <WavyBackground
        className="relative w-full h-screen overflow-hidden"
        containerClassName="relative w-full h-screen overflow-hidden"
        colors={wavyConfig.colors}
        backgroundFill={wavyConfig.backgroundFill}
        waveOpacity={wavyConfig.waveOpacity}
        blur={wavyConfig.blur}
        speed={wavyConfig.speed}
        waveWidth={60}
      >
        {currentBookIndex > 0 && (
          <div className="absolute left-4 top-1/2 transform -translate-y-1/2 z-50">
            <Button
              onClick={previousBook}
              disabled={isTransitioning}
              className="bg-white/10 border-white/30 hover:bg-white/20 disabled:opacity-50 transition-colors duration-700 rounded-full w-12 h-12 p-0"
              style={{ color: textColor }}
            >
              <ChevronLeft className="w-6 h-6" />
            </Button>
          </div>
        )}

        <div className="absolute right-4 top-1/2 transform -translate-y-1/2 z-50">
          <Button
            onClick={nextBook}
            disabled={isTransitioning}
            className="bg-white/10 border-white/30 hover:bg-white/20 disabled:opacity-50 transition-colors duration-700 rounded-full w-12 h-12 p-0"
            style={{ color: textColor }}
          >
            <ChevronRight className="w-6 h-6" />
          </Button>
        </div>

        <div className="absolute top-24 left-1/2 transform -translate-x-1/2 z-50">
          <button
            onClick={() => onNavigate(0)}
            className="text-white/60 hover:text-white transition-colors animate-bounce"
          >
            <ChevronUp className="w-6 h-6" />
          </button>
        </div>

        <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 z-50">
          <button
            onClick={() => onNavigate(2)}
            className="text-white/60 hover:text-white transition-colors animate-bounce"
          >
            <ChevronDown className="w-6 h-6" />
          </button>
        </div>

        <div className="h-full grid grid-cols-1 lg:grid-cols-2 gap-0">
          <div
            className="h-full w-full flex items-center justify-center"
            style={{
              opacity: bookOpacity,
              transition: "opacity 0.15s ease-out",
            }}
          >
            {fovReady && (
              <BookModel
                params={params}
                materialProps={materialProps}
                meshRef={object2MeshRef}
                onReady={setBookReady}
                bookIndex={currentBookIndex}
                highlightIntensity={highlightIntensity}
              />
            )}
          </div>

          <div className="h-full flex items-center justify-start p-8 lg:p-12">
            <BookDetails book={currentBook} isVisible={textVisible} textColor={textColor} />
          </div>
        </div>

      </WavyBackground>
    </div>
  )
}
