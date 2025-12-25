"use client"
import { cn } from "@/lib/utils"

interface Book3DProps {
  title: string
  subtitle?: string
  author: string
  year: string
  coverColor: string
  textColor: string
  coverDesign?: "simple" | "ornate" | "modern"
  className?: string
}

export function Book3D({
  title,
  subtitle,
  author,
  year,
  coverColor,
  textColor,
  coverDesign = "simple",
  className,
}: Book3DProps) {
  return (
    <div className={cn("relative group cursor-pointer", className)}>
      <div className="relative w-64 h-80 transform-gpu transition-all duration-700 group-hover:rotate-y-12 group-hover:scale-105">
        {/* Book Shadow */}
        <div className="absolute inset-0 bg-black/20 blur-lg transform translate-x-4 translate-y-4 scale-95" />

        {/* Book Body */}
        <div className="relative w-full h-full transform-gpu preserve-3d">
          {/* Front Cover */}
          <div
            className={cn(
              "absolute inset-0 rounded-r-lg shadow-2xl flex flex-col justify-between p-6 text-center",
              coverColor,
              textColor,
            )}
            style={{
              background:
                coverDesign === "ornate"
                  ? `linear-gradient(135deg, ${coverColor.replace("bg-", "")}, ${coverColor.replace("bg-", "").replace("500", "700")})`
                  : undefined,
            }}
          >
            {/* Title */}
            <div className="flex-1 flex flex-col justify-center">
              <h3 className={cn("font-bold leading-tight mb-2", coverDesign === "ornate" ? "text-2xl" : "text-xl")}>
                {title}
              </h3>
              {subtitle && <p className="text-sm opacity-80 mb-4">{subtitle}</p>}
            </div>

            {/* Author and Year */}
            <div className="space-y-2">
              <p className="text-sm font-medium">{author}</p>
              <p className="text-xs opacity-70">{year}</p>
            </div>

            {/* Decorative Elements */}
            {coverDesign === "ornate" && (
              <div className="absolute inset-4 border-2 border-current opacity-20 rounded" />
            )}
            {coverDesign === "modern" && (
              <div className="absolute top-4 right-4 w-8 h-8 border-2 border-current opacity-30" />
            )}
          </div>

          {/* Book Spine */}
          <div
            className={cn(
              "absolute top-0 right-0 w-4 h-full rounded-r-lg shadow-lg",
              coverColor.replace("500", "600"),
              "transform origin-left rotateY-90",
            )}
          >
            <div className="h-full w-full bg-gradient-to-r from-black/20 to-transparent" />
          </div>

          {/* Pages */}
          <div className="absolute top-1 right-1 w-60 h-78 bg-gray-100 rounded-r shadow-inner">
            <div className="h-full w-full bg-gradient-to-r from-gray-200 to-white opacity-80" />
          </div>
        </div>
      </div>
    </div>
  )
}
