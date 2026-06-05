import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const root = resolve(import.meta.dirname, "..")

function read(relativePath) {
  return readFileSync(resolve(root, relativePath), "utf8")
}

function assertIncludes(file, expected) {
  const source = read(file)
  if (!source.includes(expected)) {
    throw new Error(`${file} should include ${expected}`)
  }
}

function assertExcludes(file, unexpected) {
  const source = read(file)
  if (source.includes(unexpected)) {
    throw new Error(`${file} should not include ${unexpected}`)
  }
}

assertExcludes("app/layout.tsx", "antialiased dark")
assertExcludes("app/layout.tsx", 'body className="bg-black"')
assertExcludes("app/globals.css", "@apply bg-black text-white")
assertIncludes("app/page.tsx", "bg-[#f7fbfc]")
assertIncludes("app/sections/hero-section.tsx", 'backgroundColor="#f7fbfc"')
assertIncludes("app/sections/hero-section.tsx", 'strokeColor="#b7c9d3"')
assertIncludes("components/ui/placeholders-and-vanish-input.tsx", "bg-white/95")
assertIncludes("components/ui/gradient-button.tsx", "after:bg-white")
assertIncludes("components/book-showcase/book-showcase.tsx", 'backgroundFill: "#f7fbfc"')
assertIncludes("app/sections/knowledge-graph-section.tsx", "bg-[#f7fbfc] text-[#12323a]")
assertIncludes("app/sections/team-section.tsx", "bg-[#eef7f8]")
assertIncludes("components/ui/knowledge-graph-d3.tsx", "const lightGraphTheme")
assertIncludes("components/ui/knowledge-graph-d3.tsx", "getNodePaint")
assertIncludes("components/ui/knowledge-graph-d3.tsx", 'className="fixed inset-0 z-[9999] bg-[#f7fbfc]/96')
assertExcludes("components/ui/knowledge-graph-d3.tsx", "bg-black/80")
assertExcludes("components/ui/knowledge-graph-d3.tsx", "bg-black/98")
assertExcludes("components/ui/knowledge-graph-d3.tsx", "text-white")
assertExcludes("components/ui/knowledge-graph-d3.tsx", "#cbd5e1")
assertExcludes("components/ui/knowledge-graph-d3.tsx", "drop-shadow(0 0 8px rgba(0,0,0,0.3))")
assertIncludes("components/chat/pdf-viewer-modal.tsx", "bg-[#f7fbfc]")
assertIncludes("components/chat/pdf-viewer-modal.tsx", "bg-[#edf6f8]")
assertIncludes("components/chat/pdf-viewer-modal.tsx", "source.keyExplanation")
assertExcludes("components/chat/pdf-viewer-modal.tsx", "bg-black")
assertExcludes("components/chat/pdf-viewer-modal.tsx", "bg-gray")
assertExcludes("components/chat/pdf-viewer-modal.tsx", "text-white")
assertExcludes("components/chat/pdf-viewer-modal.tsx", "border-white")
assertExcludes("app/template.tsx", "PageTransition")
assertIncludes("app/page.tsx", "router.prefetch(\"/chat\")")
assertIncludes("app/sections/hero-section.tsx", "router.push(\"/chat\")")
assertIncludes("components/chat/conversation-sidebar.tsx", "router.prefetch(\"/\")")
assertExcludes("app/template.tsx", "bg-black")
assertExcludes("app/template.tsx", "AnimatePresence")
assertExcludes("app/template.tsx", "mode=\"wait\"")

console.log("light theme checks passed")
