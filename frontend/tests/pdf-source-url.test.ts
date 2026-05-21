import test from "node:test"
import assert from "node:assert/strict"

import { buildPdfUrl, inferPdfRelativePath } from "../lib/chat/pdf-source-url"

test("infers the source PDF from an OCR image API path", () => {
  const imageUrl =
    "/documents/image?path=%E6%A0%87%E5%87%86%E8%A7%84%E8%8C%83%2FGB%2051039-2014%20%E7%BB%BC%E5%90%88%E5%8C%BB%E9%99%A2%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E8%A7%84%E8%8C%83%2Ffull%2Fimages%2Fdemo.jpg"

  assert.equal(
    inferPdfRelativePath(undefined, imageUrl, "医院建筑设计指南"),
    "标准规范/GB 51039-2014 综合医院建筑设计规范.pdf",
  )
})

test("prefers image-derived PDF path over title fallback when building pdf url", () => {
  const imageUrl =
    "http://127.0.0.1:8010/api/v1/documents/image?path=%E6%A0%87%E5%87%86%E8%A7%84%E8%8C%83%2FGB51039-2014%E7%BB%BC%E5%90%88%E5%8C%BB%E9%99%A2%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E6%A0%87%E5%87%86%2Ffull%2Fimages%2Fdemo.jpg"

  assert.equal(
    buildPdfUrl(undefined, undefined, undefined, imageUrl, "医院建筑设计指南"),
    "http://127.0.0.1:8010/api/v1/documents/pdf?path=%E6%A0%87%E5%87%86%E8%A7%84%E8%8C%83%2FGB51039-2014%E7%BB%BC%E5%90%88%E5%8C%BB%E9%99%A2%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E6%A0%87%E5%87%86.pdf",
  )
})
