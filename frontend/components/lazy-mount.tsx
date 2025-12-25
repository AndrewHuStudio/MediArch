"use client"

import type { ReactNode } from "react"
import { useEffect, useRef, useState } from "react"

interface LazyMountProps {
  children: ReactNode
  fallback?: ReactNode
  className?: string
  rootMargin?: string
  threshold?: number | number[]
}

export default function LazyMount({
  children,
  fallback = null,
  className,
  rootMargin = "800px 0px",
  threshold = 0,
}: LazyMountProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    if (mounted) return
    const element = containerRef.current
    if (!element) return

    if (typeof IntersectionObserver === "undefined") {
      setMounted(true)
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries
        if (entry?.isIntersecting) {
          setMounted(true)
          observer.disconnect()
        }
      },
      { root: null, rootMargin, threshold },
    )

    observer.observe(element)
    return () => observer.disconnect()
  }, [mounted, rootMargin, threshold])

  return (
    <div ref={containerRef} className={className}>
      {mounted ? children : fallback}
    </div>
  )
}

