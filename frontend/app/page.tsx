"use client"

import type React from "react"

import { useState, useEffect, useRef } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import HeroSection from "@/app/sections/hero-section"
import KnowledgeSection from "@/app/sections/knowledge-section"
import KnowledgeGraphSection from "@/app/sections/knowledge-graph-section"
import TeamSection from "@/app/sections/team-section"
import { usePageTransition } from "@/components/page-transition"

export default function MediArchLanding() {
  const { startTransition } = usePageTransition()
  const router = useRouter()
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [currentSection, setCurrentSection] = useState(0)
  const [activeNav, setActiveNav] = useState("首页")
  const [headerVisible, setHeaderVisible] = useState(true)

  const isScrollingRef = useRef(false)
  const scrollTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    let debounceTimeout: NodeJS.Timeout | null = null

    const handleScroll = () => {
      if (isScrollingRef.current) return

      if (debounceTimeout) {
        clearTimeout(debounceTimeout)
      }

      debounceTimeout = setTimeout(() => {
        const currentScrollY = window.scrollY
        const sections = [0, 1, 2, 3]
        const windowHeight = window.innerHeight
        const scrollPosition = currentScrollY + windowHeight / 2

        for (let i = sections.length - 1; i >= 0; i--) {
          const sectionElement = document.getElementById(`section-${i}`)
          if (sectionElement) {
            const sectionTop = sectionElement.offsetTop
            if (scrollPosition >= sectionTop) {
              setCurrentSection(i)
              break
            }
          }
        }
      }, 100)
    }

    window.addEventListener("scroll", handleScroll, { passive: true })

    return () => {
      window.removeEventListener("scroll", handleScroll)
      if (debounceTimeout) {
        clearTimeout(debounceTimeout)
      }
    }
  }, [])

  useEffect(() => {
    void router.prefetch("/chat")

    // 预加载 ChatInterface 组件，减少跳转延迟
    import("@/components/chat/chat-interface")
  }, [router])

  const getScrollTop = () => {
    const container = scrollContainerRef.current
    if (container && container.scrollHeight > container.clientHeight + 1) {
      return container.scrollTop
    }
    return window.scrollY
  }

  const scrollToTop = () => {
    const container = scrollContainerRef.current
    if (container && container.scrollHeight > container.clientHeight + 1) {
      container.scrollTo({ top: 0, behavior: "smooth" })
      return
    }
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  const scrollToSection = (sectionIndex: number) => {
    setCurrentSection(sectionIndex)

    isScrollingRef.current = true

    if (scrollTimeoutRef.current) {
      clearTimeout(scrollTimeoutRef.current)
    }

    const sectionElement = document.getElementById(`section-${sectionIndex}`)
    if (sectionElement) {
      sectionElement.scrollIntoView({ behavior: "smooth" })

      scrollTimeoutRef.current = setTimeout(() => {
        isScrollingRef.current = false
      }, 1000)
    }
  }

  useEffect(() => {
    const navItems = ["首页", "知识库", "知识图谱", "实验室"]
    setActiveNav(navItems[currentSection] || "首页")
  }, [currentSection])

  const handleHomeLogoClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    e.stopPropagation()

    const topSection = document.getElementById("section-0")
    if (topSection) {
      const topOffset = topSection.getBoundingClientRect().top
      if (Math.abs(topOffset) <= 4) return
    } else if (getScrollTop() <= 4) {
      return
    }

    scrollToSection(0)
  }

  const handleChatNavigation = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault()
    e.stopPropagation()
    startTransition("/chat")
  }

  return (
    <div
      ref={scrollContainerRef}
      className="relative w-full min-h-screen bg-black overflow-y-auto"
      onClick={() => setHeaderVisible(true)}
    >
      <header
        className={`fixed top-0 left-0 right-0 z-50 transition-transform duration-300 ${
          headerVisible ? "translate-y-0" : "-translate-y-full"
        }`}
      >
        <div
          onClick={(e) => {
            e.stopPropagation()
            setHeaderVisible(false)
          }}
          className="absolute inset-0 bg-black/20 backdrop-blur-sm cursor-pointer -z-10"
        />
        <div className="max-w-7xl mx-auto flex items-center justify-between py-3 px-6 relative z-10">
          <button
            type="button"
            onClick={handleHomeLogoClick}
            data-nav-button
            className="inline-flex hover:opacity-80 transition-all active:scale-95"
          >
            <img src="/images/mediarch-logo.png" alt="MediArch" className="h-8" />
          </button>
          <nav className="flex items-center gap-8">
            {[
              { name: "首页", index: 0 },
              { name: "知识库", index: 1 },
              { name: "知识图谱", index: 2 },
              { name: "实验室", index: 3 },
            ].map((section) => (
              <button
                key={section.name}
                onClick={(e) => {
                  e.stopPropagation()
                  scrollToSection(section.index)
                }}
                data-nav-button
                className={`text-sm font-medium transition-colors ${
                  activeNav === section.name
                    ? "text-white border-b border-white pb-1"
                    : "text-gray-400 hover:text-white"
                }`}
              >
                {section.name}
              </button>
            ))}
            <Link
              href="/chat"
              prefetch
              onClick={handleChatNavigation}
              data-nav-button
              className={`text-sm font-medium transition-colors ${
                activeNav === "智能问答" ? "text-white border-b border-white pb-1" : "text-gray-400 hover:text-white"
              }`}
            >
              智能问答
            </Link>
          </nav>
        </div>
      </header>

      <HeroSection onNavigate={scrollToSection} />
      <KnowledgeSection onNavigate={scrollToSection} />
      <KnowledgeGraphSection onNavigate={scrollToSection} />
      <TeamSection onNavigate={scrollToSection} />
    </div>
  )
}
