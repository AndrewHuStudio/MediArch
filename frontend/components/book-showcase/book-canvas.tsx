"use client"

import type React from "react"
import { Canvas, useFrame } from "@react-three/fiber"
import { TrackballControls } from "@react-three/drei"
import { Suspense, useRef, useEffect, useState } from "react"
import type { BookParams, MaterialProps } from "./types"
import { Book } from "./book-3d-model"
import { CameraController } from "./camera-controller"

function RenderDetector({ onFirstRender }: { onFirstRender: () => void }) {
  const hasRendered = useRef(false)

  useFrame(() => {
    if (!hasRendered.current) {
      hasRendered.current = true
      onFirstRender()
    }
  })

  return null
}

export function BookModel({
  params,
  materialProps,
  meshRef,
  onReady,
  bookIndex,
  frontCoverUrl,
  backCoverUrl,
  highlightIntensity = 0,
}: {
  params: BookParams
  materialProps: MaterialProps
  meshRef: React.MutableRefObject<any>
  onReady?: (ready: boolean) => void
  bookIndex?: number
  frontCoverUrl?: string
  backCoverUrl?: string
  highlightIntensity?: number
}) {
  const controlsRef = useRef<any>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const [hasFirstRender, setHasFirstRender] = useState(false)

  useEffect(() => {
    const handleGlobalPointerUp = (event: PointerEvent) => {
      if (controlsRef.current) {
        // Try both possible method names for different drei versions
        if (typeof controlsRef.current.handlePointerUp === "function") {
          controlsRef.current.handlePointerUp(event)
        } else if (typeof controlsRef.current.onPointerUp === "function") {
          controlsRef.current.onPointerUp(event)
        }
      }
    }

    const handleGlobalMouseUp = (event: MouseEvent) => {
      if (controlsRef.current) {
        // Try both possible method names for different drei versions
        if (typeof controlsRef.current.handlePointerUp === "function") {
          controlsRef.current.handlePointerUp(event)
        } else if (typeof controlsRef.current.onPointerUp === "function") {
          controlsRef.current.onPointerUp(event)
        }
      }
    }

    // Add global listeners to relay pointer/mouse up events to controls
    window.addEventListener("pointerup", handleGlobalPointerUp)
    window.addEventListener("mouseup", handleGlobalMouseUp)

    return () => {
      window.removeEventListener("pointerup", handleGlobalPointerUp)
      window.removeEventListener("mouseup", handleGlobalMouseUp)
    }
  }, [])

  const handleBookReady = (ready: boolean) => {
    if (onReady) {
      onReady(ready && hasFirstRender)
    }
  }

  const handleFirstRender = () => {
    setHasFirstRender(true)
  }

  return (
    <div ref={canvasRef} className="w-full h-full">
      <Canvas
        className="w-full h-full"
        camera={{ position: params.cameraPosition, fov: params.cameraFov }}
        resize={{ scroll: false, debounce: { scroll: 0, resize: 0 } }}
        dpr={[1, 2]}
        legacy={true}
        shadows
      >
        <Suspense fallback={null}>
          <LightingRig highlightIntensity={highlightIntensity} targetPosition={params.position} />
          <RenderDetector onFirstRender={handleFirstRender} />
          <Book
            params={params}
            materialProps={materialProps}
            meshRef={meshRef}
            onReady={handleBookReady}
            bookIndex={bookIndex}
            frontCoverUrl={frontCoverUrl}
            backCoverUrl={backCoverUrl}
          />
          <CameraController params={params} />
          <TrackballControls
            ref={controlsRef}
            noPan={true}
            noZoom={true}
            staticMoving={false}
            dynamicDampingFactor={0.05}
            rotateSpeed={1.5}
            target={params.position}
          />
        </Suspense>
      </Canvas>
    </div>
  )
}

function LightingRig({
  highlightIntensity,
  targetPosition,
}: {
  highlightIntensity: number
  targetPosition: [number, number, number]
}) {
  const spotlightRef = useRef<any>(null)
  const fillLightRef = useRef<any>(null)

  useFrame(() => {
    if (spotlightRef.current) {
      spotlightRef.current.intensity = 1.2 + highlightIntensity * 1.6
      spotlightRef.current.angle = 0.45 + highlightIntensity * 0.12
    }
    if (fillLightRef.current) {
      fillLightRef.current.intensity = 0.5 + highlightIntensity * 0.9
    }
  })

  useEffect(() => {
    if (spotlightRef.current) {
      spotlightRef.current.target.position.set(targetPosition[0], targetPosition[1], targetPosition[2])
      spotlightRef.current.target.updateMatrixWorld()
    }
  }, [targetPosition])

  return (
    <>
      <ambientLight intensity={0.8} color="#f8fbff" />
      <hemisphereLight args={["#f5f7ff", "#0f172a", 0.45]} />
      <directionalLight position={[8, 10, 6]} intensity={0.9} color="#ffffff" />
      <directionalLight position={[-8, 6, 6]} intensity={0.7} color="#dbeafe" />
      <spotLight
        ref={spotlightRef}
        position={[3, 7, 4]}
        angle={0.5}
        penumbra={0.65}
        intensity={1.3}
        color="#fefefe"
        decay={1.2}
        castShadow
      />
      <pointLight ref={fillLightRef} position={[-4, 2, 3]} intensity={0.6} color="#eef2ff" />
    </>
  )
}
