"use client"

import { pdfjs } from "react-pdf"

// 统一使用本地 worker，避免 CDN 或跨域导致的加载问题
const workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString()
if (typeof window !== "undefined" && pdfjs.GlobalWorkerOptions.workerSrc !== workerSrc) {
  pdfjs.GlobalWorkerOptions.workerSrc = workerSrc
}

const thumbnailCache = new Map<string, Promise<string | null>>()

export async function getPdfThumbnail(
  url: string,
  pageNumber = 1,
  maxWidth = 320,
): Promise<string | null> {
  if (typeof window === "undefined") return null
  const key = `${url}#${pageNumber}#${maxWidth}`
  if (thumbnailCache.has(key)) {
    return thumbnailCache.get(key)!
  }

  const task = (async () => {
    let loadingTask: any
    try {
      loadingTask = pdfjs.getDocument({ url, withCredentials: true })
      const pdf = await loadingTask.promise
      const safePage = Math.min(Math.max(pageNumber || 1, 1), pdf.numPages || 1)
      const page = await pdf.getPage(safePage)
      const baseViewport = page.getViewport({ scale: 1 })
      const scale = Math.min(maxWidth / baseViewport.width, 1)
      const viewport = page.getViewport({ scale })

      const canvas = document.createElement("canvas")
      canvas.width = viewport.width
      canvas.height = viewport.height
      const ctx = canvas.getContext("2d")
      if (!ctx) return null

      await page.render({ canvasContext: ctx, viewport }).promise
      const dataUrl = canvas.toDataURL("image/png")
      canvas.width = canvas.height = 0
      pdf.cleanup?.()
      return dataUrl
    } catch (error) {
      console.warn("[PDF thumbnail] failed to build thumbnail", { url, pageNumber, error })
      return null
    } finally {
      try {
        loadingTask?.destroy?.()
      } catch {
        /* noop */
      }
    }
  })()

  thumbnailCache.set(key, task)
  return task
}
