"use client"

import { Badge } from "@/components/ui/badge"
import type { BookData } from "./types"

interface BookDetailsProps {
  book: BookData
  isVisible: boolean
  textColor: string
}

export function BookDetails({ book, isVisible, textColor }: BookDetailsProps) {
  const primaryTextClass = textColor === "white" ? "text-white" : "text-black"
  const secondaryTextClass = textColor === "white" ? "text-white/80" : "text-black/80"
  const mutedTextClass = textColor === "white" ? "text-white/60" : "text-black/60"

  const badgeClass =
    textColor === "white" ? "bg-white/20 text-white border-white/30" : "bg-black/20 text-black border-black/30"

  return (
    <div
      className={`space-y-6 p-8 lg:p-12 max-w-2xl mx-auto lg:px-0 lg:pr-9 transition-all duration-700 ${
        isVisible ? "opacity-100" : "opacity-0"
      }`}
      style={{
        transition: "opacity 300ms ease-out, color 700ms ease-out",
      }}
    >
      <div className="space-y-1">
        <div className="flex flex-wrap gap-2">
          {book.genres.map((genre) => (
            <Badge key={genre} variant="secondary" className={`${badgeClass} transition-colors duration-700`}>
              {genre}
            </Badge>
          ))}
        </div>

        <h1
          className={`text-4xl leading-tight my-[9px] font-medium transition-colors duration-700 ${primaryTextClass}`}
        >
          {book.title}
        </h1>
        <p className={`text-xl font-normal transition-colors duration-700 ${secondaryTextClass}`}>{book.subtitle}</p>

        <div className={`flex items-center gap-2 text-sm transition-colors duration-700 ${mutedTextClass}`}>
          <span>{book.metaLine}</span>
        </div>
      </div>

      <div className="space-y-4 max-w-xl">
        {book.description.map((paragraph, index) => (
          <p key={index} className={`leading-relaxed font-light transition-colors duration-700 ${secondaryTextClass}`}>
            {paragraph}
          </p>
        ))}
      </div>
    </div>
  )
}
