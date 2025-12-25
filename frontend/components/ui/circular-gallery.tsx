"use client"
import React, { useState, useEffect, useRef, type HTMLAttributes } from "react"
import { cn } from "@/lib/utils"
import { Maximize2, X } from 'lucide-react'
import { motion, AnimatePresence } from "framer-motion"

export interface GalleryItem {
  common: string
  binomial: string
  photo: {
    url: string
    text: string
    pos?: string
    by: string
  }
}

interface CircularGalleryProps extends HTMLAttributes<HTMLDivElement> {
  items: GalleryItem[]
  radius?: number
}

export const CircularGallery = React.forwardRef<HTMLDivElement, CircularGalleryProps>(
  ({ items, className, radius = 280, ...props }, ref) => {
    const [rotation, setRotation] = useState(0)
    const [isHovering, setIsHovering] = useState(false)
    const [rotationSpeed, setRotationSpeed] = useState(0.25)
    const [imageErrors, setImageErrors] = useState<Set<string>>(new Set())
    const animationFrameRef = useRef<number | null>(null)
    const wheelTimeoutRef = useRef<NodeJS.Timeout | null>(null)
    const [lightboxImage, setLightboxImage] = useState<string | null>(null)

    useEffect(() => {
      const autoRotate = () => {
        setRotation((prev) => (prev + rotationSpeed) % 360)
        animationFrameRef.current = requestAnimationFrame(autoRotate)
      }

      animationFrameRef.current = requestAnimationFrame(autoRotate)

      return () => {
        if (animationFrameRef.current) {
          cancelAnimationFrame(animationFrameRef.current)
        }
      }
    }, [rotationSpeed])

    const handleWheel = (e: React.WheelEvent) => {
      if (isHovering) {
        e.preventDefault()
        setRotationSpeed(3)

        if (wheelTimeoutRef.current) {
          clearTimeout(wheelTimeoutRef.current)
        }
        wheelTimeoutRef.current = setTimeout(() => {
          setRotationSpeed(0.25)
        }, 200)
      }
    }

    const handleImageError = (url: string) => {
      console.log("[v0] Failed to load image:", url)
      setImageErrors((prev) => new Set(prev).add(url))
    }

    const handleImageLoad = (url: string) => {
      console.log("[v0] Successfully loaded image:", url)
    }

    const anglePerItem = 360 / items.length

    return (
      <>
        <div
          ref={ref}
          role="region"
          aria-label="Circular 3D Gallery"
          className={cn("relative w-full h-full flex items-center justify-center", className)}
          style={{ perspective: "1800px" }}
          onMouseEnter={() => setIsHovering(true)}
          onMouseLeave={() => {
            setIsHovering(false)
            setRotationSpeed(0.15)
          }}
          onWheel={handleWheel}
          {...props}
        >
          <div
            className="relative w-full h-full"
            style={{
              transform: `rotateY(${rotation}deg)`,
              transformStyle: "preserve-3d",
              transition: "transform 0.05s linear",
            }}
          >
            {items.map((item, i) => {
              const itemAngle = i * anglePerItem
              const totalRotation = rotation % 360
              const relativeAngle = (itemAngle - totalRotation + 360) % 360
              const normalizedAngle = Math.abs(relativeAngle > 180 ? 360 - relativeAngle : relativeAngle)
              const opacity = 1
              const hasError = imageErrors.has(item.photo.url)

              return (
                <div
                  key={`${item.photo.url}-${i}`}
                  role="group"
                  aria-label={item.common}
                  className="absolute"
                  style={{
                    width: "220px",
                    height: "300px",
                    transform: `rotateY(${itemAngle}deg) translateZ(${radius}px)`,
                    left: "50%",
                    top: "50%",
                    marginLeft: "-110px",
                    marginTop: "-150px",
                    opacity: opacity,
                    transition: "opacity 0.3s linear",
                  }}
                >
                  <div className="relative w-full h-full rounded-lg shadow-2xl overflow-hidden border border-gray-700 bg-gray-900/70 backdrop-blur-lg flex flex-col group">
                    <div className="flex-1 relative overflow-hidden bg-gray-800">
                      {!hasError ? (
                        <>
                          <img
                            src={item.photo.url || "/placeholder.svg"}
                            alt={item.photo.text}
                            className="absolute inset-0 w-full h-full object-cover"
                            style={{ objectPosition: item.photo.pos || "center" }}
                            onError={() => handleImageError(item.photo.url)}
                            onLoad={() => handleImageLoad(item.photo.url)}
                          />
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setLightboxImage(item.photo.url)
                            }}
                            className="absolute bottom-2 right-2 p-1.5 rounded-md bg-black/70 hover:bg-black/90 transition-all text-white shadow-lg opacity-0 group-hover:opacity-100 z-10"
                            aria-label="展开图片"
                          >
                            <Maximize2 className="w-4 h-4" />
                          </button>
                        </>
                      ) : (
                        <div className="absolute inset-0 flex items-center justify-center bg-gray-800">
                          <div className="text-center text-gray-400">
                            <svg className="w-16 h-16 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                              />
                            </svg>
                            <p className="text-xs">{item.common}</p>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="flex-shrink-0 p-3 bg-gradient-to-t from-black/80 to-transparent text-white">
                      <h2 className="text-lg font-bold">{item.common}</h2>
                      <em className="text-xs italic opacity-80">{item.binomial}</em>
                      <p className="text-xs mt-1 opacity-70">{item.photo.text}</p>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <AnimatePresence>
          {lightboxImage && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-[200] bg-black/95 backdrop-blur-sm flex items-center justify-center p-8"
              onClick={() => setLightboxImage(null)}
            >
              <button
                onClick={() => setLightboxImage(null)}
                className="absolute top-4 right-4 p-2 rounded-lg bg-white/10 hover:bg-white/20 transition-colors text-white z-10"
                aria-label="关闭"
              >
                <X className="w-5 h-5" />
              </button>

              <motion.img
                initial={{ scale: 0.9 }}
                animate={{ scale: 1 }}
                transition={{ duration: 0.3 }}
                src={lightboxImage}
                alt="Full size view"
                className="max-w-full max-h-full object-contain rounded-lg"
                onClick={(e) => e.stopPropagation()}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </>
    )
  },
)

CircularGallery.displayName = "CircularGallery"
