import type * as THREE from "three"

export interface MaterialProps {
  color: string
  metalness: number
  roughness: number
  emissive: string
  emissiveIntensity: number
  texture: THREE.Texture | null
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
  description: string[]
  genres: string[]
  rating: number
  reviews: number
  materialProps: MaterialProps
  backgroundColor: string
  textColor: string // Added textColor property for dynamic text color transitions
}
