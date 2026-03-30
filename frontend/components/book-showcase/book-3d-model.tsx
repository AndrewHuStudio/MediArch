"use client"

import type React from "react"
import { useGLTF } from "@react-three/drei"
import { useRef, useEffect, useState } from "react"
import * as THREE from "three"
import type { BookParams, MaterialProps } from "./types"
import { debugAllObjectsUV, debugUVMapping, generateUVForBothCovers } from "./uv-debugger"
import { TexturePreloader } from "./texture-preloader"

export function Book({
  params,
  materialProps,
  meshRef,
  onReady,
  bookIndex = 0,
  frontCoverUrl,
  backCoverUrl,
}: {
  params: BookParams
  materialProps: MaterialProps
  meshRef: React.MutableRefObject<any>
  onReady?: (ready: boolean) => void
  bookIndex?: number
  frontCoverUrl?: string
  backCoverUrl?: string
}) {
  const { scene } = useGLTF("/models/book_red.glb")
  const bookRef = useRef<any>(null)
  const [bookScene, setBookScene] = useState<any>(null)
  const [object2Mesh, setObject2Mesh] = useState<any>(null)
  const [uvGenerated, setUvGenerated] = useState(false)
  const [defaultTexture, setDefaultTexture] = useState<any>(null)
  const [backCoverTexture, setBackCoverTexture] = useState<any>(null)
  const [combinedTexture, setCombinedTexture] = useState<any>(null)
  const [currentBookIndex, setCurrentBookIndex] = useState<number>(bookIndex)

  const isReady = uvGenerated && combinedTexture && object2Mesh

  useEffect(() => {
    if (onReady) {
      onReady(isReady)
    }
  }, [isReady, onReady])

  useEffect(() => {
    if (scene && !bookScene) {
      const clonedScene = scene.clone()
      setBookScene(clonedScene)
    }
  }, [scene, bookScene])

  useEffect(() => {
    if (bookIndex !== currentBookIndex || (!defaultTexture && !backCoverTexture)) {
      setCurrentBookIndex(bookIndex)
      setDefaultTexture(null)
      setBackCoverTexture(null)
      setCombinedTexture(null)

      const loader = new THREE.TextureLoader()
      const preloader = TexturePreloader.getInstance()

      const loadTextureWithFallback = (
        path: string,
        onLoad: (texture: any) => void,
        onError: (error: any) => void,
      ) => {
        // Try to use preloaded image first
        const preloadedImg = preloader.getPreloadedImage(path)
        if (preloadedImg) {
          const texture = new THREE.Texture(preloadedImg)
          texture.flipY = true
          texture.colorSpace = THREE.SRGBColorSpace
          texture.wrapS = THREE.RepeatWrapping
          texture.wrapT = THREE.RepeatWrapping
          texture.minFilter = THREE.LinearFilter
          texture.magFilter = THREE.LinearFilter
          texture.generateMipmaps = false
          texture.needsUpdate = true
          onLoad(texture)
          return
        }

        // Fallback to regular THREE.TextureLoader
        loader.load(path, onLoad, undefined, onError)
      }

      const frontCoverPath =
        frontCoverUrl && frontCoverUrl.length > 0
          ? frontCoverUrl
          : materialProps.texture?.front || "/images/standard-front.png"

      const backCoverPath =
        backCoverUrl && backCoverUrl.length > 0
          ? backCoverUrl
          : materialProps.texture?.back || "/images/standard-back.png"

      loadTextureWithFallback(
        frontCoverPath,
        (texture) => {
          setDefaultTexture(texture)
          console.log(`Front cover texture loaded for book ${bookIndex}`)
        },
        (error) => {
          console.error(`Failed to load front cover texture for book ${bookIndex}:`, error)
        },
      )

      loadTextureWithFallback(
        backCoverPath,
        (texture) => {
          setBackCoverTexture(texture)
          console.log(`Back cover texture loaded for book ${bookIndex}`)
        },
        (error) => {
          console.error(`Failed to load back cover texture for book ${bookIndex}:`, error)
        },
      )
    }
  }, [bookIndex, currentBookIndex, defaultTexture, backCoverTexture, materialProps.texture])

  useEffect(() => {
    if (bookScene && !uvGenerated) {
      try {
        debugAllObjectsUV(bookScene)
      } catch (error) {
        console.log("Error during UV analysis:", error)
      }

      const sketchfabModel = bookScene.getObjectByName("Sketchfab_model")
      if (sketchfabModel) {
        const geode = sketchfabModel.getObjectByName("Geode")
        if (geode) {
          const object2 = geode.getObjectByName("Object_2")
          if (object2 && (object2 as any).material) {
            const mesh = object2 as any
            console.log("Found Object_2 - book cover material loaded")

            generateUVForBothCovers(mesh)
            setUvGenerated(true)
            console.log("UV coordinates generated for both front and back covers")

            console.log("object.name:", mesh.name)
            console.log("material.type:", mesh.material.constructor.name)
            console.log("material.map:", (mesh.material as any).map)
            debugUVMapping(mesh)

            setObject2Mesh(mesh)
            meshRef.current = mesh
          }
        }
      }
    }
  }, [bookScene, meshRef, uvGenerated])

  const createCombinedTexture = (frontTexture: any, backTexture: any) => {
    const canvas = document.createElement("canvas")
    const ctx = canvas.getContext("2d")
    if (!ctx) return null

    // Set canvas size to accommodate both textures side by side
    canvas.width = frontTexture.image.width * 2
    canvas.height = frontTexture.image.height

    // Draw front cover on left half
    ctx.drawImage(frontTexture.image, 0, 0, frontTexture.image.width, frontTexture.image.height)

    // Draw back cover on right half
    ctx.drawImage(backTexture.image, frontTexture.image.width, 0, backTexture.image.width, backTexture.image.height)

    // Create texture from combined canvas
    const combined = new THREE.CanvasTexture(canvas)
    combined.flipY = true
    combined.colorSpace = THREE.SRGBColorSpace
    combined.wrapS = THREE.ClampToEdgeWrapping
    combined.wrapT = THREE.ClampToEdgeWrapping
    combined.minFilter = THREE.LinearFilter
    combined.magFilter = THREE.LinearFilter
    combined.generateMipmaps = false
    combined.needsUpdate = true

    return combined
  }

  useEffect(() => {
    if (defaultTexture && backCoverTexture) {
      const combined = createCombinedTexture(defaultTexture, backCoverTexture)
      if (combined) {
        setCombinedTexture(combined)
        console.log("Combined front and back cover texture created")
      }
    }
  }, [defaultTexture, backCoverTexture])

  const applyMaterialToMesh = (mesh: any, props: MaterialProps) => {
    let material = mesh.userData.originalMaterial || mesh.material
    if (Array.isArray(material)) {
      material = material[0]
    }

    const clonedMaterial = material.clone()

    if (props.texture) {
      clonedMaterial.map = props.texture
      props.texture.offset.set(props.offsetX, props.offsetY)
      clonedMaterial.color.setRGB(1, 1, 1)
      clonedMaterial.metalness = 0.4
      clonedMaterial.roughness = 1
      clonedMaterial.emissive.setRGB(0, 0, 0)
      clonedMaterial.emissiveIntensity = 0
      clonedMaterial.vertexColors = false
      clonedMaterial.transparent = false
      clonedMaterial.opacity = 1
      clonedMaterial.visible = true
      // Added polygon offset to prevent z-fighting between cover and page surfaces
      clonedMaterial.polygonOffset = true
      clonedMaterial.polygonOffsetFactor = -1 // pull slightly toward the camera
      clonedMaterial.polygonOffsetUnits = -1
      clonedMaterial.needsUpdate = true

      props.texture.colorSpace = THREE.SRGBColorSpace
      props.texture.flipY = true
    } else {
      clonedMaterial.map = null
      clonedMaterial.color.set(props.color)
      clonedMaterial.emissive.set(props.emissive)
      clonedMaterial.emissiveIntensity = props.emissiveIntensity
      clonedMaterial.metalness = props.metalness
      clonedMaterial.roughness = props.roughness
      clonedMaterial.transparent = false
      clonedMaterial.opacity = 1
      clonedMaterial.visible = true
      // Added polygon offset for non-texture materials as well
      clonedMaterial.polygonOffset = true
      clonedMaterial.polygonOffsetFactor = -1
      clonedMaterial.polygonOffsetUnits = -1
      clonedMaterial.needsUpdate = true
    }

    mesh.material = clonedMaterial
  }

  useEffect(() => {
    if (object2Mesh && combinedTexture && uvGenerated) {
      console.log("Applying combined front and back cover textures to Object_2")
      const combinedProps = { ...materialProps, texture: combinedTexture }
      applyMaterialToMesh(object2Mesh, combinedProps)
    }
  }, [object2Mesh, materialProps, combinedTexture, uvGenerated])

  if (!isReady || !bookScene) {
    return null
  }

  return (
    <primitive
      ref={bookRef}
      object={bookScene}
      scale={params.scale}
      position={params.position}
      rotation={params.rotation}
    />
  )
}
