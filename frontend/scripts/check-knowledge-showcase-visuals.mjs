import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"

const __dirname = dirname(fileURLToPath(import.meta.url))
const showcase = readFileSync(resolve(__dirname, "../components/book-showcase/book-showcase.tsx"), "utf8")
const bookCanvas = readFileSync(resolve(__dirname, "../components/book-showcase/book-canvas.tsx"), "utf8")
const bookModel = readFileSync(resolve(__dirname, "../components/book-showcase/book-3d-model.tsx"), "utf8")

const checks = [
  {
    name: "knowledge background uses light clinical wave palettes",
    pass: /KNOWLEDGE_WAVE_PALETTES/.test(showcase) && !/"#0f172a"/.test(showcase) && !/"#1e293b"/.test(showcase),
  },
  {
    name: "knowledge background avoids dark wave opacity bands",
    pass: !/waveOpacity:\s*0\.[5-9]/.test(showcase) && /waveOpacity:\s*0\.2[0-9]/.test(showcase),
  },
  {
    name: "book canvas uses a brighter lighting baseline",
    pass: /ambientLight intensity=\{1\.15\}/.test(bookCanvas) && /hemisphereLight args=\{\["#ffffff", "#dbeafe", 0\.75\]\}/.test(bookCanvas),
  },
  {
    name: "textured book covers render as paper instead of grey metal",
    pass:
      /clonedMaterial\.metalness\s*=\s*0\.05/.test(bookModel) &&
      /clonedMaterial\.roughness\s*=\s*0\.78/.test(bookModel) &&
      /clonedMaterial\.emissiveIntensity\s*=\s*0\.04/.test(bookModel),
  },
]

const failed = checks.filter((check) => !check.pass)

if (failed.length > 0) {
  console.error("Knowledge showcase visual checks failed:")
  for (const check of failed) {
    console.error(`- ${check.name}`)
  }
  process.exit(1)
}

console.log("Knowledge showcase visual checks passed.")
