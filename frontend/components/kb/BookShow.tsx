"use client"

import { Suspense, useRef, useState, useEffect } from "react"
import dynamic from "next/dynamic"
import { TexturePreloader } from "@/components/book-showcase/texture-preloader"
import type { BookParams, MaterialProps } from "@/components/book-showcase/types"

const BookCanvas = dynamic(
  () => import("@/components/book-showcase/book-canvas").then((mod) => ({ default: mod.BookModel })),
  {
    ssr: false,
    loading: () => (
      <div className="w-full h-full flex items-center justify-center bg-gray-100 rounded-lg">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-2"></div>
          <p className="text-gray-500 text-sm">加载 3D 引擎...</p>
        </div>
      </div>
    ),
  },
)

interface BookShowProps {
  coverFront: string
  coverBack: string
  bookIndex?: number
}

export function BookShow({ coverFront, coverBack, bookIndex = 0 }: BookShowProps) {
  const meshRef = useRef<any>(null)
  const [isReady, setIsReady] = useState(false)
  const [texturesLoaded, setTexturesLoaded] = useState(false)
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)

  const defaultParams: BookParams = {
    scale: [1, 1, 1],
    position: [0, 0, 0],
    rotation: [0, 0, 0],
    cameraPosition: [0, 0, 5],
    cameraFov: 50,
  }

  const defaultMaterialProps: MaterialProps = {
    color: "#ffffff",
    metalness: 0.1,
    roughness: 0.8,
    emissive: "#000000",
    emissiveIntensity: 0,
    texture: null,
    offsetX: 0,
    offsetY: 0,
  }

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)")
    setPrefersReducedMotion(mediaQuery.matches)

    const handleChange = (e: MediaQueryListEvent) => {
      setPrefersReducedMotion(e.matches)
    }

    mediaQuery.addEventListener("change", handleChange)
    return () => mediaQuery.removeEventListener("change", handleChange)
  }, [])

  useEffect(() => {
    const preloader = TexturePreloader.getInstance()

    const loadTextures = async () => {
      try {
        await Promise.all([preloader.preloadImage(coverFront), preloader.preloadImage(coverBack)])
        setTexturesLoaded(true)
        console.log(`[BookShow] Textures loaded for book ${bookIndex}`)
      } catch (error) {
        console.warn(`[BookShow] Failed to preload textures for book ${bookIndex}:`, error)
        // Still allow rendering with fallback textures
        setTexturesLoaded(true)
      }
    }

    loadTextures()
  }, [coverFront, coverBack, bookIndex])

  const handleBookReady = (ready: boolean) => {
    setIsReady(ready)
    console.log(`[BookShow] Book ${bookIndex} ready:`, ready)
  }

  if (!texturesLoaded) {
    return (
      <div className="w-full h-full bg-gray-100 rounded-lg flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-2"></div>
          <p className="text-gray-500 text-sm">加载 3D 书籍模型...</p>
        </div>
      </div>
    )
  }

  if (prefersReducedMotion) {
    return (
      <div className="w-full h-full bg-gradient-to-br from-gray-50 to-gray-100 rounded-lg overflow-hidden flex items-center justify-center">
        <div className="text-center">
          <div className="w-32 h-40 bg-white rounded-lg shadow-lg mb-4 flex items-center justify-center border">
            <img
              src={coverFront || "/placeholder.svg"}
              alt="Book Cover"
              className="w-full h-full object-cover rounded-lg"
              onError={(e) => {
                const target = e.target as HTMLImageElement
                target.src = "/images/default-fallback.jpeg"
              }}
            />
          </div>
          <p className="text-gray-500 text-sm">静态模式 (已禁用动画)</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full h-full bg-gradient-to-br from-gray-50 to-gray-100 rounded-lg overflow-hidden">
      <Suspense
        fallback={
          <div className="w-full h-full flex items-center justify-center">
            <div className="text-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-2"></div>
              <p className="text-gray-500 text-sm">渲染 3D 模型...</p>
            </div>
          </div>
        }
      >
        <BookCanvas
          params={defaultParams}
          materialProps={defaultMaterialProps}
          meshRef={meshRef}
          onReady={handleBookReady}
          bookIndex={bookIndex}
          frontCoverUrl={coverFront}
          backCoverUrl={coverBack}
        />
      </Suspense>

      {!isReady && texturesLoaded && (
        <div className="absolute inset-0 bg-gray-100 bg-opacity-75 flex items-center justify-center">
          <div className="text-center">
            <div className="animate-pulse rounded-lg bg-gray-300 h-32 w-24 mx-auto mb-2"></div>
            <p className="text-gray-500 text-sm">准备 3D 书籍...</p>
          </div>
        </div>
      )}
    </div>
  )
}
