export interface MaterialProps {
  color: string
  metalness: number
  roughness: number
  emissive: string
  emissiveIntensity: number
  texture: any
  offsetX: number
  offsetY: number
}

export interface BookParams {
  scale: [number, number, number]
  position: [number, number, number]
  rotation: [number, number, number]
  cameraPosition: [number, number, number]
  cameraFov: number
}

export interface BookData {
  id: string
  title: string
  subtitle: string
  author: string
  publishedYear: number
  pages: number
  metaLine: string
  countLabel: string
  description: string[]
  genres: string[]
  rating: number
  reviews: number
  materialProps: MaterialProps
  backgroundColor: string
  textColor: string // Added textColor property for dynamic text color transitions
}
