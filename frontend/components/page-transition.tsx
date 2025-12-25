"use client"

import type { ReactNode } from "react"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { AnimatePresence, MotionConfig, motion } from "framer-motion"
import { usePathname, useRouter } from "next/navigation"

interface PageTransitionProps {
  children: ReactNode
}

interface PageTransitionContextValue {
  startTransition: (href: string) => void
  isTransitioning: boolean
}

const TRANSITION_DURATION = 200

const PageTransitionContext = createContext<PageTransitionContextValue | null>(null)

export function usePageTransition() {
  const context = useContext(PageTransitionContext)
  if (!context) {
    throw new Error("usePageTransition must be used within PageTransition")
  }
  return context
}

export function PageTransition({ children }: PageTransitionProps) {
  const router = useRouter()
  const pathname = usePathname()
  const [overlayVisible, setOverlayVisible] = useState(false)
  const [overlayOpacity, setOverlayOpacity] = useState(0)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const pendingPathRef = useRef<string | null>(null)
  const enterTimerRef = useRef<NodeJS.Timeout | null>(null)
  const safetyTimerRef = useRef<NodeJS.Timeout | null>(null)
  const firstRenderRef = useRef(true)

  const clearTimers = useCallback(() => {
    if (enterTimerRef.current) {
      clearTimeout(enterTimerRef.current)
      enterTimerRef.current = null
    }
    if (safetyTimerRef.current) {
      clearTimeout(safetyTimerRef.current)
      safetyTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      clearTimers()
    }
  }, [clearTimers])

  const fadeToBlack = useCallback(() => {
    setOverlayVisible(true)
    setOverlayOpacity(1)
  }, [])

  const fadeToTransparent = useCallback(() => {
    setOverlayOpacity(0)
    if (enterTimerRef.current) {
      clearTimeout(enterTimerRef.current)
    }
    enterTimerRef.current = setTimeout(() => {
      setOverlayVisible(false)
      setIsTransitioning(false)
      pendingPathRef.current = null
    }, TRANSITION_DURATION)
  }, [])

  const startTransition = useCallback(
    (targetPath: string) => {
      if (!targetPath || targetPath === pathname) return
      if (pendingPathRef.current) return

      clearTimers()
      pendingPathRef.current = targetPath
      setIsTransitioning(true)
      fadeToBlack()
      router.push(targetPath)

      safetyTimerRef.current = setTimeout(() => {
        if (pendingPathRef.current !== targetPath) return
        setOverlayOpacity(0)
        setOverlayVisible(false)
        setIsTransitioning(false)
        pendingPathRef.current = null
      }, 5000)
    },
    [clearTimers, fadeToBlack, pathname, router],
  )

  useEffect(() => {
    if (firstRenderRef.current) {
      firstRenderRef.current = false
      return
    }

    if (pendingPathRef.current) {
      fadeToTransparent()
    }
  }, [fadeToTransparent, pathname])

  const contextValue = useMemo<PageTransitionContextValue>(
    () => ({
      startTransition,
      isTransitioning,
    }),
    [isTransitioning, startTransition],
  )

  return (
    <PageTransitionContext.Provider value={contextValue}>
      <MotionConfig reducedMotion="user">
        <div className="relative w-full h-full overflow-hidden">
          {overlayVisible && (
            <div
              aria-hidden="true"
              className={`fixed inset-0 z-[70] bg-black transition-opacity duration-200 ease-in-out ${
                overlayOpacity > 0 ? "pointer-events-auto" : "pointer-events-none"
              }`}
              style={{ opacity: overlayOpacity }}
            />
          )}

          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={pathname}
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.98 }}
              transition={{ duration: 0.3, ease: "easeInOut" }}
              className="w-full h-full"
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </div>
      </MotionConfig>
    </PageTransitionContext.Provider>
  )
}
