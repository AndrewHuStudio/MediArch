"use client"

import { booksData } from "@/components/book-showcase/book-data"
import Link from "next/link"
import Image from "next/image"
import { Badge } from "@/components/ui/badge"

export default function BooksPage() {
  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="max-w-6xl mx-auto px-6">
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold text-gray-900 mb-4">Book Collection</h1>
          <p className="text-xl text-gray-600">Explore our curated selection of books</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {booksData.map((book) => (
            <Link
              key={book.id}
              href={`/book/${book.id}`}
              className="group block bg-white rounded-lg shadow-md hover:shadow-xl transition-all duration-300 overflow-hidden"
            >
              <div className="aspect-[3/4] relative overflow-hidden">
                <Image
                  src={`/images/${book.id}-front-cover.jpeg`}
                  alt={`${book.title} cover`}
                  fill
                  className="object-cover group-hover:scale-105 transition-transform duration-300"
                />
              </div>

              <div className="p-6">
                <div className="flex flex-wrap gap-1 mb-3">
                  {book.genres.slice(0, 2).map((genre) => (
                    <Badge key={genre} variant="secondary" className="text-xs">
                      {genre}
                    </Badge>
                  ))}
                </div>

                <h3 className="text-xl font-bold text-gray-900 mb-2 group-hover:text-blue-600 transition-colors">
                  {book.title}
                </h3>
                <p className="text-gray-600 text-sm mb-2">{book.subtitle}</p>
                <p className="text-gray-500 text-sm">{book.metaLine}</p>

                <div className="flex items-center justify-between mt-4">
                  <div className="flex items-center gap-1">
                    <span className="text-yellow-500">★</span>
                    <span className="text-sm font-medium">{book.rating}</span>
                  </div>
                  <span className="text-sm text-gray-500">{book.countLabel}</span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
