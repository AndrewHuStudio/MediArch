"use client"
import { cn } from "@/lib/utils"

interface BookProps {
  title: string
  author?: string
  category: string
  color?: string
  className?: string
  onClick?: () => void
}

export function Book({ title, author, category, color = "from-white to-gray-800", className, onClick }: BookProps) {
  return (
    <div
      className={cn(
        "group relative cursor-pointer transform transition-all duration-300 hover:scale-105 hover:-translate-y-2",
        className,
      )}
      onClick={onClick}
    >
      {/* Book spine */}
      <div className={`w-12 h-48 bg-gradient-to-b ${color} rounded-r-sm shadow-lg relative overflow-hidden`}>
        {/* Book spine highlight */}
        <div className="absolute top-0 left-0 w-1 h-full bg-white/20" />
        <div className="absolute top-0 right-0 w-1 h-full bg-black/20" />

        {/* Book title on spine */}
        <div className="absolute inset-0 flex flex-col justify-center items-center p-2">
          <div className="text-xs font-bold text-black/80 transform -rotate-90 whitespace-nowrap truncate max-w-40">
            {title}
          </div>
        </div>
      </div>

      {/* Book cover (visible on hover) */}
      <div
        className={`absolute top-0 left-0 w-32 h-48 bg-gradient-to-br ${color} rounded-sm shadow-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 transform -translate-x-20 group-hover:-translate-x-24 z-10`}
      >
        <div className="p-4 h-full flex flex-col justify-between">
          <div>
            <h3 className="text-sm font-bold text-black/90 leading-tight mb-2">{title}</h3>
            {author && <p className="text-xs text-black/70">{author}</p>}
          </div>
          <div className="text-xs text-black/60 font-medium">{category}</div>
        </div>

        {/* Cover highlight */}
        <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-br from-white/10 to-transparent rounded-sm" />
      </div>
    </div>
  )
}

interface BookshelfProps {
  books: BookProps[]
  className?: string
}

export function Bookshelf({ books, className }: BookshelfProps) {
  return (
    <div className={cn("flex items-end gap-1 p-4", className)}>
      {books.map((book, index) => (
        <Book key={index} {...book} />
      ))}
    </div>
  )
}
