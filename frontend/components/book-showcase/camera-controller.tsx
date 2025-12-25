"use client"

import { useFrame, useThree } from "@react-three/fiber"
import { useRef } from "react"
import type { BookParams } from "./types"

export function CameraController({ params }: { params: BookParams }) {
  const { camera } = useThree()
  const prevParams = useRef(params)

  useFrame(() => {
    if (
      prevParams.current.cameraPosition !== params.cameraPosition ||
      prevParams.current.cameraFov !== params.cameraFov
    ) {
      camera.position.set(...params.cameraPosition)
      camera.fov = params.cameraFov
      camera.updateProjectionMatrix()
      prevParams.current = params
    }
  })

  return null
}
