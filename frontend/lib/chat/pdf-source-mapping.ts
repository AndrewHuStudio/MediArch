import type { PDFSource } from "../../components/chat/pdf-source-card"
import type { Citation } from "../api/types"
import { buildImageUrl, buildPdfUrl } from "./pdf-source-url"

function normalizeCitationPositions(cite: Citation): PDFSource["positions"] | undefined {
  if (!Array.isArray(cite.positions) || cite.positions.length === 0) return undefined

  const positions = cite.positions
    .map((pos: any) => {
      if (!pos) return null
      if (Array.isArray(pos.bbox)) {
        return { page: pos.page ?? cite.page_number ?? 1, bbox: pos.bbox as number[] }
      }
      if (
        typeof pos.x === "number" &&
        typeof pos.y === "number" &&
        typeof pos.width === "number" &&
        typeof pos.height === "number"
      ) {
        return {
          page: pos.page ?? cite.page_number ?? 1,
          bbox: [pos.x, pos.y, pos.x + pos.width, pos.y + pos.height],
        }
      }
      return null
    })
    .filter(Boolean) as NonNullable<PDFSource["positions"]>

  return positions.length > 0 ? positions : undefined
}

export function citationsToPDFSources(citations: Citation[]): PDFSource[] {
  return citations.map((cite, index) => {
    const documentPath = cite.document_path || cite.documentPath
    const filePath = cite.file_path || cite.filePath
    const rawImageUrl = cite.image_url || cite.imageUrl
    const imageUrl = buildImageUrl(rawImageUrl)
    const rawPdfPath = cite.pdf_url || cite.pdfUrl
    const pdfUrl = buildPdfUrl(rawPdfPath, documentPath, filePath, rawImageUrl, cite.source)

    return {
      id: cite.chunk_id || `pdf-${index}`,
      title: cite.source,
      pageNumber: cite.page_number || 1,
      snippet: cite.snippet,
      highlightText: cite.highlight_text || cite.snippet,
      positions: normalizeCitationPositions(cite),
      pdfUrl,
      documentPath,
      filePath,
      imageUrl,
      thumbnail: imageUrl,
      section: cite.section || cite.sub_section,
      metadata: cite.metadata,
      docId: cite.doc_id,
      pageValue: cite.page_value || cite.pageValue,
      keyExplanation: cite.key_explanation || cite.keyExplanation,
      contentType: ((cite.content_type as any) || (rawImageUrl ? "image" : undefined)) as any,
    }
  })
}
