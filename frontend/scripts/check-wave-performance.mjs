import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"

const __dirname = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(resolve(__dirname, "../components/ui/wave-background.tsx"), "utf8")

const checks = [
  {
    name: "caps the animation loop below full 60fps",
    pass: /DEFAULT_MAX_FPS\s*=\s*30/.test(source) && /FRAME_INTERVAL_MS/.test(source),
  },
  {
    name: "speeds up perceived motion without raising the frame cap",
    pass: /WAVE_TIME_SCALE\s*=\s*1\.45/.test(source),
  },
  {
    name: "pauses while the wave is offscreen",
    pass: /IntersectionObserver/.test(source),
  },
  {
    name: "pauses while the browser tab is hidden",
    pass: /document\.visibilityState/.test(source),
  },
  {
    name: "keeps line density close to the original visual design",
    pass: /BASE_LINE_GAP\s*=\s*10/.test(source),
  },
  {
    name: "uses a moderate point density with smoothed paths",
    pass: /BASE_POINT_GAP\s*=\s*14/.test(source) && /buildSmoothPath/.test(source) && / Q /.test(source),
  },
  {
    name: "does not keep the old dense 8px grid constants",
    pass: !/const\s+xGap\s*=\s*8/.test(source) && !/const\s+yGap\s*=\s*8/.test(source),
  },
]

const failed = checks.filter((check) => !check.pass)

if (failed.length > 0) {
  console.error("Wave performance checks failed:")
  for (const check of failed) {
    console.error(`- ${check.name}`)
  }
  process.exit(1)
}

console.log("Wave performance checks passed.")
