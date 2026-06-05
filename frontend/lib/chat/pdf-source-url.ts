import { getApiUrl } from "../api"

const API_IMAGE_PATH_RE = /(?:\/api\/v1)?\/documents\/image\?path=([^&#]+)/i
const OCR_IMAGE_PATH_RE = /documents_ocr\/([^/]+)\/([^/]+)\/(?:full\/)?images\//i
const RELATIVE_IMAGE_PATH_RE = /^([^/]+)\/([^/]+)\/(?:full\/)?images\//i

export function buildImageUrl(rawImagePath?: string) {
  if (!rawImagePath) return undefined

  const normalized = rawImagePath.replace(/\\/g, "/")
  if (/^(https?:)?\/\//i.test(normalized) || normalized.startsWith("data:")) {
    return normalized
  }
  if (normalized.startsWith("/api/")) {
    return getApiUrl(normalized.replace(/^\/api\/v1/, ""))
  }
  if (normalized.startsWith("/documents/image")) {
    return getApiUrl(normalized)
  }

  const match = normalized.match(/documents_ocr\/(.+)$/i)
  const relative = match ? match[1] : normalized.replace(/^\/+/, "")
  return getApiUrl(`/documents/image?path=${encodeURIComponent(relative)}`)
}

function inferPdfRelativePathFromImagePath(imagePath?: string) {
  const normalizedImage = String(imagePath || "").replace(/\\/g, "/").trim()
  if (!normalizedImage) return undefined

  const apiMatch = normalizedImage.match(API_IMAGE_PATH_RE)
  if (apiMatch?.[1]) {
    const decoded = decodeURIComponent(apiMatch[1]).replace(/\\/g, "/")
    const ocrMatch = decoded.match(OCR_IMAGE_PATH_RE)
    if (ocrMatch) return `${ocrMatch[1]}/${ocrMatch[2]}.pdf`

    const relMatch = decoded.match(RELATIVE_IMAGE_PATH_RE)
    if (relMatch) return `${relMatch[1]}/${relMatch[2]}.pdf`
  }

  const ocrMatch = normalizedImage.match(OCR_IMAGE_PATH_RE)
  if (ocrMatch) return `${ocrMatch[1]}/${ocrMatch[2]}.pdf`

  const relMatch = normalizedImage.match(RELATIVE_IMAGE_PATH_RE)
  if (relMatch) return `${relMatch[1]}/${relMatch[2]}.pdf`

  return undefined
}

export function inferPdfRelativePath(fallbackPath?: string, imagePath?: string, title?: string) {
  const normalizedFallback = String(fallbackPath || "").replace(/\\/g, "/").trim()
  if (normalizedFallback) {
    const match = normalizedFallback.match(/documents\/(.+)$/i)
    return match ? match[1] : normalizedFallback.replace(/^\/+/, "")
  }

  const inferredFromImage = inferPdfRelativePathFromImagePath(imagePath)
  if (inferredFromImage) {
    return inferredFromImage
  }

  const normalizedTitle = String(title || "").trim().replace(/\.pdf$/i, "")
  if (normalizedTitle) {
    return `书籍报告/${normalizedTitle}.pdf`
  }

  return undefined
}

export function buildPdfUrl(
  rawPdfPath?: string,
  documentPath?: string,
  filePath?: string,
  imagePath?: string,
  title?: string,
) {
  const toApiUrl = (path: string) => getApiUrl(path.startsWith("/") ? path : `/${path}`)

  const normalizeRelativePath = (path: string) => {
    if (path.startsWith("/api/v1/")) return path.replace(/^\/api\/v1/, "")
    return path
  }

  const resolvePath = (path?: string) => {
    if (!path) return undefined
    const normalized = normalizeRelativePath(path)
    const isAbsolute = /^https?:\/\//i.test(normalized)
    if (isAbsolute) {
      return normalized
    }
    return toApiUrl(normalized)
  }

  const fromPdfUrl = resolvePath(rawPdfPath)
  if (fromPdfUrl) return fromPdfUrl

  const relative = inferPdfRelativePath(documentPath || filePath, imagePath, title)
  if (relative) {
    return toApiUrl(`/documents/pdf?path=${encodeURIComponent(relative)}`)
  }

  return undefined
}
