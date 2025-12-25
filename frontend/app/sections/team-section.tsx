"use client"

import { CircularGallery, type GalleryItem } from "@/components/ui/circular-gallery"
import LazyMount from "@/components/lazy-mount"
import { ChevronUp } from "lucide-react"
import { motion, useInView } from "framer-motion"
import { useRef } from "react"

interface TeamSectionProps {
  onNavigate: (sectionIndex: number) => void
}

export default function TeamSection({ onNavigate }: TeamSectionProps) {
  const sectionRef = useRef(null)
  const isInView = useInView(sectionRef, {
    amount: 0.3,
    once: false, // Allow re-triggering when scrolling back
  })

  const teamMembers: GalleryItem[] = [
    {
      common: "Qi Yi",
      binomial: "Architect and Professor",
      photo: {
        url: "/images/qiyi.jpg",
        text: "Chief Executive of Design X",
        by: "Design X Group",
      },
    },
    {
      common: "Mauricio",
      binomial: "Architect and Professor",
      photo: {
        url: "/images/mauricio.jpg",
        text: "Foreign Lecturer of Design X",
        by: "Design X Group",
      },
    },
    {
      common: "Daria",
      binomial: "Architect and Professor",
      photo: {
        url: "/images/daria.jpg",
        text: "Foreign Lecturer of Design X",
        by: "Design X Group",
      },
    },
    {
      common: "Hu Shi Li",
      binomial: "Postgraduate student",
      photo: {
        url: "/images/hushili.jpg",
        text: "Developer of Design X",
        by: "Design X Group",
      },
    },
    {
      common: "Position Open",
      binomial: "Reserver of Design X",
      photo: {
        url: "/images/position-open.jpg",
        text: "Welcome to join us",
        by: "Design X Group",
      },
    },
    {
      common: "Position Open",
      binomial: "Reserver of Design X",
      photo: {
        url: "/images/position-open.jpg",
        text: "Welcome to join us",
        by: "Design X Group",
      },
    },
  ]

  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: 0.2,
        delayChildren: 0.1,
      },
    },
  }

  const lightBarVariants = {
    collapsed: { opacity: 0, scaleX: 0 },
    expanded: {
      opacity: 1,
      scaleX: 1,
      transition: { duration: 1.2 },
    },
  }

  const highlightVariants = {
    collapsed: { opacity: 0, y: -4 },
    expanded: {
      opacity: 0.9,
      y: 0,
      transition: { duration: 0.55 },
    },
  }

  const glowVariants = {
    collapsed: { opacity: 0, scaleY: 0.2 },
    expanded: {
      opacity: 1,
      scaleY: 1,
      transition: { duration: 1.2, delay: 0.2 },
    },
  }

  return (
    <section id="section-3" ref={sectionRef} className="relative h-screen flex flex-col bg-slate-950 overflow-hidden">
      {/* Layer 1: Background light effect layer */}
      <div
        className="pointer-events-none absolute inset-0 z-0
        [background:radial-gradient(120%_80%_at_50%_0%,rgba(34,211,238,0.18),transparent_55%),
        radial-gradient(80%_60%_at_0%_0%,rgba(2,6,23,0.65),transparent_60%),
        radial-gradient(80%_60%_at_100%_0%,rgba(2,6,23,0.65),transparent_60%)]"
      />

      {/* Layer 1: Light bar + glow */}
      <div className="relative z-0 flex flex-col items-center gap-3 pt-[13vh]">
        <motion.div
          animate={isInView ? "expanded" : "collapsed"}
          variants={containerVariants}
          className="relative w-[min(78rem,90vw)] overflow-visible"
        >
          {/* Top light bar */}
          <motion.div
            variants={lightBarVariants}
            style={{ transformOrigin: "50% 50%" }}
            className="relative h-[5px] w-full bg-cyan-300 rounded-[2px]
                      shadow-[0_2px_30px_rgba(34,211,238,0.9),0_8px_80px_rgba(34,211,238,0.55)]"
          />

          {/* Top highlight */}
          <motion.div
            variants={highlightVariants}
            className="pointer-events-none absolute -top-[1px] inset-x-0 h-px
                      [background:linear-gradient(to_right,transparent,rgba(255,255,255,0.9)_10%,rgba(255,255,255,0.9)_90%,transparent)] opacity-90"
          />

          {/* Bottom glow */}
          <motion.div
            variants={glowVariants}
            style={{ transformOrigin: "top" }}
            className="pointer-events-none absolute top-full left-1/2 -translate-x-1/2
                      h-[50vh] w-[125%]
                      [background:radial-gradient(80%_110%_at_50%_0%,rgba(34,211,238,0.45),rgba(34,211,238,0.18)_40%,transparent_72%)]
                      blur-[22px]"
          />
        </motion.div>
      </div>

      <div className="relative z-[70] flex flex-col items-center gap-3 mt-[6vh]">
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.7, duration: 0.8 }}
          className="
            relative z-[90] inline-block leading-[1.08] pb-[0.1em]
            bg-clip-text text-transparent [-webkit-text-fill-color:transparent]
            text-[clamp(2rem,5vw,3.5rem)] font-extrabold tracking-tight
            bg-[linear-gradient(to_bottom,_#ffffff_0%,_#e9ecef_25%,_#c5cbd3_55%,_#5b636d_100%)]
          "
        >
          Design X Group
        </motion.h1>
      </div>

      {/* Gallery section with updated styling */}
      <div className="relative z-50 flex-1 flex items-center justify-center">
        <LazyMount
          className="w-[clamp(600px,70vw,900px)] h-[clamp(350px,45vh,500px)] -translate-y-4 md:-translate-y-6"
          fallback={<div className="h-full w-full rounded-2xl border border-white/10 bg-white/5 backdrop-blur" />}
        >
          <CircularGallery items={teamMembers} radius={280} />
        </LazyMount>
      </div>

      {/* Navigation button */}
      <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 flex flex-col items-center gap-2 z-50">
        <button
          onClick={() => onNavigate(2)}
          data-nav-button
          className="text-white/60 hover:text-white transition-colors"
        >
          <ChevronUp className="w-6 h-6" />
        </button>
      </div>
    </section>
  )
}
