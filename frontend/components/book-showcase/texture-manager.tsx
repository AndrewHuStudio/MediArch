import type React from "react"
import * as THREE from "three"
import type { MaterialProps } from "./types"

export const handleImageUpload = (
  event: React.ChangeEvent<HTMLInputElement>,
  setMaterialProps: React.Dispatch<React.SetStateAction<MaterialProps>>,
) => {
  const file = event.target.files?.[0]
  if (file) {
    console.log(`Uploading texture: ${file.name}, Size: ${file.size} bytes, Type: ${file.type}`)

    const reader = new FileReader()
    reader.onload = (e) => {
      const dataUrl = e.target?.result as string

      const loader = new THREE.TextureLoader()
      loader.load(
        dataUrl,
        (texture) => {
          console.log(`Texture loaded successfully with dimensions: ${texture.image.width}x${texture.image.height}`)

          texture.flipY = true
          texture.colorSpace = THREE.SRGBColorSpace
          texture.wrapS = THREE.RepeatWrapping
          texture.wrapT = THREE.RepeatWrapping
          texture.minFilter = THREE.LinearFilter
          texture.magFilter = THREE.LinearFilter
          texture.generateMipmaps = false

          setMaterialProps((prev) => ({
            ...prev,
            texture,
            color: "#ffffff",
          }))
        },
        undefined,
        (error) => {
          console.error("TextureLoader failed to load texture:", error)
        },
      )
    }
    reader.readAsDataURL(file)
  }
}

export const clearTexture = (
  setMaterialProps: React.Dispatch<React.SetStateAction<MaterialProps>>,
  defaultMaterialProps: MaterialProps,
) => {
  setMaterialProps((prev) => ({
    ...prev,
    texture: null,
    color: defaultMaterialProps.color,
  }))
  console.log("Texture cleared")
}
