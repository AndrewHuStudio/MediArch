"use client"

import { useState, useEffect } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ShoppingCart, Heart, Share2, CheckCircle, ChevronLeft, ChevronRight } from "lucide-react"
import { booksData } from "@/components/book-showcase/book-data"
import { notFound } from "next/navigation"
import Image from "next/image"

interface BookProductPageProps {
  params: {
    id: string
  }
}

export default function BookProductPage({ params }: BookProductPageProps) {
  const [currentImageIndex, setCurrentImageIndex] = useState(0)
  const book = booksData.find((b) => b.id === params.id)

  if (!book) {
    notFound()
  }

  // Mock images for the book - in a real app these would come from the book data
  const bookImages = [`/images/${book.id}-front-cover.jpeg`, `/images/${book.id}-back-cover.jpeg`]

  const nextImage = () => {
    setCurrentImageIndex((prev) => (prev + 1) % bookImages.length)
  }

  const previousImage = () => {
    setCurrentImageIndex((prev) => (prev === 0 ? bookImages.length - 1 : prev - 1))
  }

  // Set background color based on book theme
  useEffect(() => {
    document.body.style.backgroundColor = book.backgroundColor
    document.body.style.transition = "background-color 0.3s ease"

    return () => {
      document.body.style.backgroundColor = ""
      document.body.style.transition = ""
    }
  }, [book.backgroundColor])

  const isDarkTheme = book.textColor === "white"
  const textColorClass = isDarkTheme ? "text-white" : "text-black"
  const secondaryTextClass = isDarkTheme ? "text-white/80" : "text-black/80"
  const mutedTextClass = isDarkTheme ? "text-white/60" : "text-black/60"
  const badgeClass = isDarkTheme
    ? "bg-white/20 text-white border-white/30 hover:bg-white/30"
    : "bg-black/10 text-black border-black/20 hover:bg-black/20"
  const buttonClass = isDarkTheme
    ? "bg-white/10 border-white/30 text-white hover:bg-white/20"
    : "bg-black/10 border-black/30 text-black hover:bg-black/20"

  return (
    <div className="min-h-screen relative">
      {/* Navigation arrows */}
      <button
        onClick={previousImage}
        className={`fixed left-4 top-1/2 -translate-y-1/2 z-10 p-2 rounded-full ${buttonClass} transition-all hover:scale-110`}
      >
        <ChevronLeft className="w-5 h-5" />
      </button>

      <button
        onClick={nextImage}
        className={`fixed right-4 top-1/2 -translate-y-1/2 z-10 p-2 rounded-full ${buttonClass} transition-all hover:scale-110`}
      >
        <ChevronRight className="w-5 h-5" />
      </button>

      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center min-h-screen">
          {/* Book Image Section */}
          <div className="flex justify-center items-center">
            <div className="relative group">
              <div className="relative w-80 h-96 lg:w-96 lg:h-[480px]">
                <Image
                  src={bookImages[currentImageIndex] || "/placeholder.svg"}
                  alt={`${book.title} cover`}
                  fill
                  className="object-contain drop-shadow-2xl transition-transform duration-300 group-hover:scale-105"
                  priority
                />
              </div>

              {/* Image indicators */}
              {bookImages.length > 1 && (
                <div className="flex justify-center mt-4 gap-2">
                  {bookImages.map((_, index) => (
                    <button
                      key={index}
                      onClick={() => setCurrentImageIndex(index)}
                      className={`w-2 h-2 rounded-full transition-all ${
                        index === currentImageIndex
                          ? isDarkTheme
                            ? "bg-white"
                            : "bg-black"
                          : isDarkTheme
                            ? "bg-white/30"
                            : "bg-black/30"
                      }`}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Book Details Section */}
          <div className="space-y-6">
            {/* Genre badges */}
            <div className="flex flex-wrap gap-2">
              {book.genres.map((genre) => (
                <Badge key={genre} className={badgeClass}>
                  {genre}
                </Badge>
              ))}
            </div>

            {/* Title and subtitle */}
            <div className="space-y-2">
              <h1 className={`text-4xl lg:text-5xl font-bold leading-tight ${textColorClass}`}>{book.title}</h1>
              <p className={`text-xl lg:text-2xl font-light ${secondaryTextClass}`}>{book.subtitle}</p>
            </div>

            {/* Author and publication info */}
            <div className={`flex items-center gap-2 text-sm ${mutedTextClass}`}>
              <span>{book.metaLine}</span>
            </div>

            {/* Description */}
            <div className="space-y-4 max-w-xl">
              {book.description.map((paragraph, index) => (
                <p key={index} className={`leading-relaxed ${secondaryTextClass}`}>
                  {paragraph}
                </p>
              ))}
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-3">
              <Button size="lg" className={`flex-1 ${buttonClass} transition-all hover:scale-105`}>
                <ShoppingCart className="w-5 h-5 mr-2" />
                Add to Cart
              </Button>
              <Button size="lg" variant="outline" className={buttonClass}>
                <Heart className="w-5 h-5 mr-2" />
                Wishlist
              </Button>
              <Button size="lg" variant="outline" className={buttonClass}>
                <Share2 className="w-5 h-5" />
              </Button>
            </div>

            {/* Rating and availability */}
            <div className="grid grid-cols-2 gap-6 pt-6">
              <div className="text-center">
                <div className={`text-3xl font-bold ${textColorClass}`}>{book.rating}</div>
                <div className={`text-lg ${isDarkTheme ? "text-yellow-400" : "text-yellow-600"}`}>★★★★★</div>
                <div className={`text-sm ${mutedTextClass}`}>{book.reviews.toLocaleString()} reviews</div>
              </div>

              <div className="text-center">
                <CheckCircle className={`mx-auto mb-2 w-8 h-8 ${isDarkTheme ? "text-green-400" : "text-green-600"}`} />
                <div className={`text-lg font-semibold ${textColorClass}`}>Available</div>
                <div className={`text-sm ${mutedTextClass}`}>In stock</div>
              </div>
            </div>
          </div>
        </div>
      </div>

    </div>
  )
}
