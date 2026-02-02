"use client"

import { useState } from "react"
import { FileText, ChevronRight } from 'lucide-react'
import { motion, AnimatePresence } from "framer-motion"
import { cn } from "@/lib/utils"

export interface PDFSource {
  id: string
  title: string
  pageNumber: number
  snippet: string
  highlightText: string
  imageUrl?: string
  thumbnail?: string
}

interface PDFSourceTabProps {
  source: PDFSource
  index: number
  onClick: () => void
}

export function PDFSourceTab({ source, index, onClick }: PDFSourceTabProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.1 }}
      onMouseEnter={() => setIsExpanded(true)}
      onMouseLeave={() => setIsExpanded(false)}
      onClick={onClick}
      className="absolute cursor-pointer"
      style={{
        top: `${index * 90}px`,
        right: "-32px", // Position outside the message bubble
        zIndex: 10 + index,
      }}
    >
      <div className="flex items-stretch h-[80px]">
        {/* Expanded thumbnail preview (left side) */}
        <AnimatePresence>
          {isExpanded && (
            <motion.div
              initial={{ width: 0, opacity: 0, x: 10 }}
              animate={{ width: 120, opacity: 1, x: 0 }}
              exit={{ width: 0, opacity: 0, x: 10 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="overflow-hidden rounded-l-md"
            >
              <div className="w-[120px] h-full bg-gray-900/98 backdrop-blur-md border border-gray-700 border-r-0 shadow-xl overflow-hidden">
                {source.thumbnail ? (
                  <img 
                    src={source.thumbnail || "/placeholder.svg"} 
                    alt={source.title} 
                    className="w-full h-full object-cover" 
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center bg-gray-800/80">
                    <FileText className="w-8 h-8 text-gray-600" />
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <motion.div
          animate={{
            backgroundColor: isExpanded ? "rgba(59, 130, 246, 0.9)" : "rgba(249, 115, 22, 0.85)",
          }}
          transition={{ duration: 0.2 }}
          className={cn(
            "w-8 h-[80px] rounded-r-md flex flex-col items-center justify-between py-2 px-1",
            "border border-l-0 shadow-lg backdrop-blur-sm",
            isExpanded ? "border-blue-400/50" : "border-orange-400/50"
          )}
        >
          <ChevronRight 
            className={cn(
              "w-3.5 h-3.5 text-white transition-transform duration-200 flex-shrink-0",
              isExpanded && "rotate-180"
            )} 
          />
          
          <div className="flex-1 flex items-center justify-center overflow-hidden py-1">
            <div className="writing-mode-vertical text-[10px] font-medium text-white whitespace-nowrap tracking-wide">
              {source.title}
            </div>
          </div>
          
          <div className="text-[9px] text-white font-semibold bg-black/20 px-1 py-0.5 rounded flex-shrink-0">
            P{source.pageNumber}
          </div>
        </motion.div>
      </div>
    </motion.div>
  )
}
