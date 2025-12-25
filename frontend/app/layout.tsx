import type React from "react"
import type { Metadata } from "next"
import { GeistSans } from "geist/font/sans"
import { GeistMono } from "geist/font/mono"
import "./globals.css"

export const metadata: Metadata = {
  title: "MediArch AI",
  description: "医疗建筑设计智能问答系统 - 基于知识图谱的医疗建筑设计助手",
  generator: "MediArch",
}

const geistSans = GeistSans.variable
const geistMono = GeistMono.variable

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans} ${geistMono} antialiased dark`}>
      <head>
        <link rel="preload" href="/images/standard-front.png" as="image" />
        <link rel="preload" href="/images/policy-front.png" as="image" />
        <link rel="preload" href="/images/book-front.png" as="image" />
        <link rel="preload" href="/images/paper-front.png" as="image" />
        <link rel="preload" href="/images/online-cases-front.png" as="image" />
      </head>
      <body className="bg-black" suppressHydrationWarning>
        {children}
      </body>
    </html>
  )
}
