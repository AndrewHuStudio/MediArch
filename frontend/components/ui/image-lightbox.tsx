"use client"

import { useState, useEffect } from "react"
import { createPortal } from "react-dom"
import { X, ZoomIn, ZoomOut, Maximize2, Download } from 'lucide-react'
import { cn } from "@/lib/utils"

interface ImageLightboxProps {
  src: string
  alt: string
  className?: string
}

export function ImageLightbox({ src, alt, className }: ImageLightboxProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [scale, setScale] = useState(1)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden'
      document.body.style.position = 'relative'
    } else {
      document.body.style.overflow = ''
      document.body.style.position = ''
    }
    return () => {
      document.body.style.overflow = ''
      document.body.style.position = ''
    }
  }, [isOpen])

  const handleZoomIn = () => {
    setScale((prev) => Math.min(prev + 0.5, 3))
  }

  const handleZoomOut = () => {
    setScale((prev) => Math.max(prev - 0.5, 0.5))
  }

  const handleClose = () => {
    setIsOpen(false)
    setScale(1)
  }

  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation()
    console.log('[v0] Downloading image:', src)
    const link = document.createElement('a')
    link.href = src
    link.download = alt || 'medical-image'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const handleExpandClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    console.log('[v0] Expand button clicked, opening modal for:', src)
    setIsOpen(true)
  }

  const modalContent = (
    <div
      className="fixed inset-0 bg-black/95 backdrop-blur-sm flex items-center justify-center p-4"
      style={{
        zIndex: 99999,
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
      }}
      onClick={handleClose}
    >
      {/* Controls Bar */}
      <div className="absolute top-4 right-4 flex items-center gap-2 z-10">
        <button
          onClick={handleDownload}
          className="p-2.5 rounded-lg bg-white/10 hover:bg-white/20 transition-colors text-white backdrop-blur-sm"
          title="下载图片"
        >
          <Download className="w-5 h-5" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            handleZoomOut()
          }}
          className="p-2.5 rounded-lg bg-white/10 hover:bg-white/20 transition-colors text-white backdrop-blur-sm"
          title="缩小"
        >
          <ZoomOut className="w-5 h-5" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            handleZoomIn()
          }}
          className="p-2.5 rounded-lg bg-white/10 hover:bg-white/20 transition-colors text-white backdrop-blur-sm"
          title="放大"
        >
          <ZoomIn className="w-5 h-5" />
        </button>
        <button
          onClick={handleClose}
          className="p-2.5 rounded-lg bg-white/10 hover:bg-white/20 transition-colors text-white backdrop-blur-sm"
          title="关闭"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Image Container */}
      <div
        className="relative w-full h-full flex items-center justify-center"
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={src || "/placeholder.svg"}
          alt={alt}
          className="max-w-full max-h-full object-contain select-none"
          style={{
            transform: `scale(${scale})`,
            transition: 'transform 0.3s ease',
            cursor: scale > 1 ? 'grab' : 'default'
          }}
          draggable={false}
        />
      </div>

      {/* Image Caption */}
      {alt && (
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 bg-black/70 backdrop-blur-sm px-6 py-3 rounded-full max-w-md">
          <p className="text-white text-sm font-medium text-center truncate">{alt}</p>
        </div>
      )}
    </div>
  )

  return (
    <>
      {/* Image with Expand Button */}
      <div className="relative my-4 block group max-w-xs">
        <img 
          src={src || "/placeholder.svg"} 
          alt={alt} 
          className={cn("w-full h-auto rounded-lg shadow-md border border-white/10", className)} 
        />
        <button
          onClick={handleExpandClick}
          className="absolute bottom-2 right-2 p-1.5 rounded-md bg-black/70 hover:bg-black/90 transition-all duration-200 text-white shadow-lg border border-white/20 hover:scale-110 flex items-center justify-center"
          style={{ zIndex: 10 }}
          title="全屏查看"
        >
          <Maximize2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {mounted && isOpen && createPortal(modalContent, document.body)}
    </>
  )
}
