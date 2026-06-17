const citationClassName =
  "inline-flex items-center align-middle text-[#034b63] text-[11px] font-bold px-1.5 py-0.5 rounded-md bg-[#dff5fb] border border-[#0891b2] cursor-pointer hover:bg-[#c7edf7] transition-colors mx-0.5 leading-none shadow-[0_0_0_1px_rgba(255,255,255,0.65)]"

const inlineReferenceClassName =
  "inline-block align-baseline text-[10px] font-medium text-gray-400 tracking-[0.02em]"

const inlineReferencePattern = /(\(|（)(\d{1,2}-\d{1,2})(\)|）)/g
const imagePlaceholderPattern = /\n{0,2}\[image:(\d+)\]\n{0,2}/g

const tableSeparatorPattern = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/
const leadingCollapsedSeparatorPattern = /^(\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?)([\s\S]+)$/
const listItemPattern = /^\s*(?:[-*+]\s+|\d+[.\u3001\uFF0E]\s+)/
const orderedListItemPattern = /^\d+[.\u3001\uFF0E]\s+/
const atxHeadingPattern = /^#{1,6}\s+/
const sectionHeadingPattern = /^[一二三四五六七八九十]+[、.．]\s*/
const anchorOnlyPattern = /^<a\s+id="[^"]+"\s*><\/a>$/

export const parseCitationNumbers = (raw: string | null): number[] => {
  if (!raw) return []
  const parts = raw
    .split(/[,\s/，、]+/)
    .map((t) => t.trim())
    .filter(Boolean)

  const numbers: number[] = []

  for (const part of parts) {
    if (part.includes("-")) {
      const [startRaw, endRaw] = part.split("-", 2)
      const start = Number.parseInt(startRaw, 10)
      const end = Number.parseInt(endRaw, 10)
      if (!Number.isFinite(start)) continue
      if (!Number.isFinite(end)) {
        numbers.push(start)
        continue
      }
      const min = Math.min(start, end)
      const max = Math.max(start, end)
      for (let n = min; n <= max; n++) numbers.push(n)
      continue
    }

    const single = Number.parseInt(part, 10)
    if (Number.isFinite(single)) numbers.push(single)
  }

  return numbers
}

const normalizeCitationNumbers = (numbers: number[], maxCitation: number) => {
  const filtered = numbers.filter((num) => Number.isFinite(num) && num > 0 && num <= maxCitation)
  const ordered: number[] = []
  const seen = new Set<number>()
  filtered.forEach((num) => {
    if (!seen.has(num)) {
      seen.add(num)
      ordered.push(num)
    }
  })
  return ordered
}

const extractCitationNumbers = (raw: string) => {
  const matches = raw.matchAll(/\[(\d+(?:\s*-\s*\d+)?(?:[\/,，、]\s*\d+)*)\]/g)
  const numbers: number[] = []
  for (const match of matches) {
    numbers.push(...parseCitationNumbers(match[1]))
  }
  return numbers
}

const buildCitationTag = (number: number) =>
  `<span data-citation="${number}" class="${citationClassName}">${number}</span>`

const buildCitationTags = (numbers: number[], maxCitation: number) => {
  const normalized = normalizeCitationNumbers(numbers, maxCitation)
  if (normalized.length === 0) return ""
  return normalized.map(buildCitationTag).join("")
}

const buildInlineReferenceTag = (raw: string) =>
  `<span data-inline-reference="true" class="${inlineReferenceClassName}">${raw}</span>`

const replaceInlineReferences = (input: string) => input.replace(inlineReferencePattern, (raw) => buildInlineReferenceTag(raw))

const replaceCitations = (input: string, maxCitation: number) => {
  if (maxCitation <= 0) return input

  return input
    .replace(/(?:\[(?:\d+\s*-\s*\d+|\d+)\][ \t]*){2,}/g, (raw) => {
      const trailingWhitespace = raw.match(/[ \t]+$/)?.[0] ?? ""
      return `${buildCitationTags(extractCitationNumbers(raw), maxCitation)}${trailingWhitespace}`
    })
    .replace(/\[(\d+(?:\s*-\s*\d+)?(?:[\/,，、]\s*\d+)*)\]/g, (raw, content) => {
      const numbers = parseCitationNumbers(content)
      const rendered = buildCitationTags(numbers, maxCitation)
      return rendered || raw
    })
}

const transformOutsideCodeFences = (text: string, transform: (segment: string) => string) => {
  const codeFenceRegex = /```[\s\S]*?```/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let result = ""

  while ((match = codeFenceRegex.exec(text)) !== null) {
    result += transform(text.slice(lastIndex, match.index))
    result += match[0]
    lastIndex = match.index + match[0].length
  }

  result += transform(text.slice(lastIndex))
  return result
}

const findNextNonEmptyTrimmed = (lines: string[], startIndex: number) => {
  for (let i = startIndex; i < lines.length; i++) {
    const trimmed = lines[i].trim()
    if (trimmed) return trimmed
  }
  return ""
}

const isTopLevelBoundaryLine = (line: string, nextNonEmptyTrimmed: string) => {
  if (/^\s/.test(line)) return false

  const trimmed = line.trim()
  if (!trimmed) return false
  if (orderedListItemPattern.test(trimmed)) return true
  if (atxHeadingPattern.test(trimmed)) return true
  if (sectionHeadingPattern.test(trimmed)) return true
  if (anchorOnlyPattern.test(trimmed) && atxHeadingPattern.test(nextNonEmptyTrimmed)) return true

  return false
}

const parseTableCells = (line: string) => {
  let normalized = line.trim()
  if (!normalized.includes("|")) return []
  if (normalized.startsWith("|")) normalized = normalized.slice(1)
  if (normalized.endsWith("|")) normalized = normalized.slice(0, -1)
  return normalized.split("|").map((cell) => cell.trim())
}

const formatTableRow = (cells: string[]) => `| ${cells.join(" | ")} |`

const splitCollapsedRowBlock = (block: string, columnCount: number): string[] => {
  const normalized = block.trim()
  if (!normalized || normalized.includes("\n") || columnCount <= 0) return [block.trimEnd()]

  const cells = parseTableCells(normalized)
  if (cells.length === 0) return [block.trimEnd()]

  const rows: string[] = []
  let index = 0

  while (index < cells.length) {
    while (index < cells.length && cells[index] === "") index += 1
    if (index >= cells.length) break

    const rowCells = cells.slice(index, Math.min(index + columnCount, cells.length))
    while (rowCells.length < columnCount) {
      rowCells.push("")
    }
    rows.push(formatTableRow(rowCells))
    index += rowCells.length
  }

  return rows.length > 0 ? rows : [block.trimEnd()]
}

const splitCollapsedSeparatorLine = (line: string): string[] | null => {
  const trimmed = line.trim()
  if (!trimmed.startsWith("|") || tableSeparatorPattern.test(trimmed)) return null

  const match = leadingCollapsedSeparatorPattern.exec(trimmed)
  if (!match) return null

  const separator = match[1].trimEnd()
  const trailingRows = match[2].trim()
  if (!trailingRows) return null

  const columnCount = parseTableCells(separator).length
  const rows = splitCollapsedRowBlock(trailingRows, columnCount)
  return [separator, ...rows]
}

const splitCollapsedTableLine = (line: string): string[] => {
  if (!line.includes("|")) return [line]
  if (tableSeparatorPattern.test(line.trim())) return [line]

  const expandedSeparatorLine = splitCollapsedSeparatorLine(line)
  if (expandedSeparatorLine) return expandedSeparatorLine

  const separatorStart = line.search(/\|\s*:?-{3,}:?\s*\|/)
  const firstPipeIndex = line.indexOf("|")
  if (separatorStart <= 0 || firstPipeIndex < 0 || separatorStart <= firstPipeIndex) return [line]

  const prefix = line.slice(0, firstPipeIndex).trimEnd()
  const header = line.slice(firstPipeIndex, separatorStart).trimEnd()
  const rest = line.slice(separatorStart).trim()
  if (!header || !rest) return [line]

  const columnCount = parseTableCells(header).length
  const trailingRows = rest.includes("\n")
    ? rest.split("\n").map((row) => row.trimEnd()).filter(Boolean)
    : splitCollapsedRowBlock(rest, columnCount)
  const rows = [header, ...trailingRows]
  if (rows.length < 2) return [line]

  const result: string[] = []
  if (prefix) {
    result.push(prefix, "")
  }
  result.push(...rows)
  result.push("")
  return result
}

export const normalizeMarkdownTables = (text: string) =>
  transformOutsideCodeFences(text, (segment) =>
    {
      const lines = segment.split("\n")
      const out: string[] = []

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i]
        const nextLine = lines[i + 1]
        const expandedNextLine = nextLine ? splitCollapsedSeparatorLine(nextLine) : null

        if (line.includes("|") && nextLine && (tableSeparatorPattern.test(nextLine.trim()) || expandedNextLine)) {
          const firstPipeIndex = line.indexOf("|")
          if (firstPipeIndex > 0) {
            const prefix = line.slice(0, firstPipeIndex).trimEnd()
            const header = line.slice(firstPipeIndex).trim()
            if (prefix) {
              out.push(prefix, "")
            }
            out.push(header)
            if (expandedNextLine) {
              out.push(...expandedNextLine)
            } else {
              out.push(nextLine.trimEnd())
            }
            i += 1
            continue
          }
        }

        if (
          listItemPattern.test(line.trim()) &&
          nextLine &&
          nextLine.trim().startsWith("|") &&
          lines[i + 2] &&
          tableSeparatorPattern.test(lines[i + 2].trim())
        ) {
          out.push(line, "")
          continue
        }

        out.push(...splitCollapsedTableLine(line))
      }

      return out.join("\n").replace(/\n{3,}/g, "\n\n")
    }
  )

export const normalizeOrderedListHierarchy = (text: string) =>
  transformOutsideCodeFences(text, (segment) => {
    const lines = segment.split("\n")
    const out: string[] = []
    let insideOrderedItem = false

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]
      const trimmed = line.trim()
      const nextNonEmptyTrimmed = findNextNonEmptyTrimmed(lines, i + 1)

      if (!trimmed) {
        out.push(line)
        continue
      }

      if (!/^\s/.test(line) && orderedListItemPattern.test(trimmed)) {
        insideOrderedItem = true
        out.push(line)
        continue
      }

      if (insideOrderedItem && isTopLevelBoundaryLine(line, nextNonEmptyTrimmed)) {
        insideOrderedItem = false
      }

      if (insideOrderedItem && !/^\s/.test(line)) {
        out.push(`   ${line}`)
        continue
      }

      out.push(line)
    }

    return out.join("\n").replace(/\n{3,}/g, "\n\n")
  })

export const applyInlineReferenceMarkup = (text: string) => transformOutsideCodeFences(text, replaceInlineReferences)

export const applyCitationMarkup = (text: string, maxCitation: number) => {
  if (maxCitation <= 0) return text

  return transformOutsideCodeFences(text, (segment) => replaceCitations(segment, maxCitation))
}

export const replaceImagePlaceholders = (text: string, availableImageCount?: number) =>
  transformOutsideCodeFences(text, (segment) =>
    segment
      .replace(/(^[ \t]*)\[image:(\d+)\][ \t]*$/gm, (_raw, indent, indexRaw) => {
        const index = Number.parseInt(indexRaw, 10)
        if (!Number.isFinite(index) || index < 0) {
          return ""
        }
        if (typeof availableImageCount === "number" && index >= availableImageCount) {
          return ""
        }
        return `${indent}<figure data-chat-image-index="${index}"></figure>`
      })
      .replace(imagePlaceholderPattern, (_raw, indexRaw) => {
        const index = Number.parseInt(indexRaw, 10)
        if (!Number.isFinite(index) || index < 0) {
          return "\n\n"
        }
        if (typeof availableImageCount === "number" && index >= availableImageCount) {
          return "\n\n"
        }
        return `\n\n<figure data-chat-image-index="${index}"></figure>\n\n`
      })
  ).replace(/\n{3,}/g, "\n\n")

export const buildMarkdownDisplayContent = (text: string, maxCitation: number, availableImageCount?: number) => {
  const normalizedTables = normalizeMarkdownTables(text)
  const normalizedLists = normalizeOrderedListHierarchy(normalizedTables)
  const withInlineReferences = applyInlineReferenceMarkup(normalizedLists)
  const withImages = replaceImagePlaceholders(withInlineReferences, availableImageCount)
  return applyCitationMarkup(withImages, maxCitation)
}
