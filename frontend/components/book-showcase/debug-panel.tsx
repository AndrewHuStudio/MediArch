"use client"

import type React from "react"

import { Button } from "@/components/ui/button"
import { Copy, RotateCcw } from "lucide-react"
import type { BookParams, MaterialProps } from "./types"
import { generateUVForObject2 } from "./uv-debugger"
import { handleImageUpload, clearTexture } from "./texture-manager"

interface DebugPanelProps {
  debugMode: boolean
  params: BookParams
  materialProps: MaterialProps
  defaultMaterialProps: MaterialProps
  object2MeshRef: React.MutableRefObject<any>
  setParams: React.Dispatch<React.SetStateAction<BookParams>>
  setMaterialProps: React.Dispatch<React.SetStateAction<MaterialProps>>
  resetParams: () => void
  copyParams: () => void
}

export function DebugPanel({
  debugMode,
  params,
  materialProps,
  defaultMaterialProps,
  object2MeshRef,
  setParams,
  setMaterialProps,
  resetParams,
  copyParams,
}: DebugPanelProps) {
  const updateMaterialProp = (prop: keyof MaterialProps, value: any) => {
    setMaterialProps((prev) => ({
      ...prev,
      [prop]: value,
    }))
    console.log(`Updated material prop ${prop} to ${value}`)
  }

  const updateParam = (param: keyof BookParams, index: number, value: any) => {
    setParams((prev) => ({
      ...prev,
      [param]:
        param === "cameraPosition" || param === "position" || param === "rotation" || param === "scale"
          ? (prev[param] as number[]).map((v, i) => (i === index ? value : v)) as any
          : prev[param],
    }))
    console.log(`Updated param ${param} at index ${index} to ${value}`)
  }

  if (!debugMode) return null

  return (
    <div className="hidden lg:block fixed top-16 right-4 z-40 w-80 bg-black/80 backdrop-blur-sm border border-white/20 rounded-lg p-4 space-y-4 max-h-[80vh] overflow-y-auto">
      <div className="flex items-center justify-between">
        <h3 className="text-white font-medium">Book Controls</h3>
        <div className="flex gap-2">
          <Button
            onClick={resetParams}
            variant="outline"
            size="sm"
            className="bg-transparent border-white/30 text-white hover:bg-white/10"
          >
            <RotateCcw className="w-4 h-4 mr-1" />
            Reset
          </Button>
          <Button
            onClick={copyParams}
            variant="outline"
            size="sm"
            className="bg-transparent border-white/30 text-white hover:bg-white/10"
          >
            <Copy className="w-4 h-4 mr-1" />
            Copy
          </Button>
        </div>
      </div>

      <div className="space-y-3 text-sm border-t border-white/20 pt-4">
        <h4 className="text-white font-medium">Object_2 Material (Book Cover)</h4>

        <div>
          <Button
            onClick={() => generateUVForObject2(object2MeshRef.current)}
            variant="outline"
            size="sm"
            className="w-full bg-transparent border-white/30 text-white hover:bg-white/10 text-xs mb-2"
          >
            Generate UV for Object_2
          </Button>
          <div className="text-white/50 text-xs">Creates planar UV mapping if Object_2 has no UV coordinates</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Texture Upload</label>
          <input
            type="file"
            accept="image/*"
            onChange={(e) => handleImageUpload(e, setMaterialProps)}
            className="w-full text-white/70 text-xs bg-transparent border border-white/30 rounded p-1"
          />
          {materialProps.texture && (
            <div className="text-white/50 text-xs mt-1">
              Texture loaded ✓ (Use 512x512, 1024x1024, or 2048x2048 for best results)
            </div>
          )}
          {materialProps.texture && (
            <Button
              onClick={() => clearTexture(setMaterialProps, defaultMaterialProps)}
              variant="outline"
              size="sm"
              className="mt-2 bg-transparent border-white/30 text-white hover:bg-white/10 text-xs"
            >
              Clear Texture
            </Button>
          )}
        </div>

        <div>
          <label className="text-white/70 block mb-1">
            Color {materialProps.texture && "(Auto: White when texture applied)"}
          </label>
          <input
            type="color"
            value={materialProps.texture ? "#ffffff" : materialProps.color}
            onChange={(e) => updateMaterialProp("color", e.target.value)}
            disabled={!!materialProps.texture}
            className="w-full h-8 rounded border border-white/30"
          />
          <div className="text-white/50 text-xs mt-1">
            {materialProps.texture ? "#ffffff (texture mode)" : materialProps.color}
          </div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Metalness</label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={materialProps.metalness}
            onChange={(e) => updateMaterialProp("metalness", Number.parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="text-white/50 text-xs">{materialProps.metalness.toFixed(2)}</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Roughness</label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={materialProps.roughness}
            onChange={(e) => updateMaterialProp("roughness", Number.parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="text-white/50 text-xs">{materialProps.roughness.toFixed(2)}</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Emissive Color</label>
          <input
            type="color"
            value={materialProps.emissive}
            onChange={(e) => updateMaterialProp("emissive", e.target.value)}
            className="w-full h-8 rounded border border-white/30"
          />
          <div className="text-white/50 text-xs mt-1">{materialProps.emissive}</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Emissive Intensity</label>
          <input
            type="range"
            min="0"
            max="2"
            step="0.01"
            value={materialProps.emissiveIntensity}
            onChange={(e) => updateMaterialProp("emissiveIntensity", Number.parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="text-white/50 text-xs">{materialProps.emissiveIntensity.toFixed(2)}</div>
        </div>

        {(materialProps.texture || (!materialProps.texture && defaultMaterialProps.texture === null)) && (
          <>
            {/* Show offset controls when texture exists OR when using default texture */}
            <div>
              <label className="text-white/70 block mb-1">Texture Offset X</label>
              <input
                type="range"
                min="-1"
                max="1"
                step="0.01"
                value={materialProps.offsetX}
                onChange={(e) => updateMaterialProp("offsetX", Number.parseFloat(e.target.value))}
                className="w-full"
              />
              <div className="text-white/50 text-xs">{materialProps.offsetX.toFixed(2)}</div>
            </div>

            <div>
              <label className="text-white/70 block mb-1">Texture Offset Y</label>
              <input
                type="range"
                min="-1"
                max="1"
                step="0.01"
                value={materialProps.offsetY}
                onChange={(e) => updateMaterialProp("offsetY", Number.parseFloat(e.target.value))}
                className="w-full"
              />
              <div className="text-white/50 text-xs">{materialProps.offsetY.toFixed(2)}</div>
            </div>
          </>
        )}
      </div>

      <div className="space-y-3 text-sm border-t border-white/20 pt-4">
        <h4 className="text-white font-medium">Transform Controls</h4>

        <div>
          <label className="text-white/70 block mb-1">Scale</label>
          {params.scale.map((val, i) => (
            <input
              key={`scale-${i}`}
              type="range"
              min="0.5"
              max="5"
              step="0.1"
              value={val}
              onChange={(e) => updateParam("scale", i, Number.parseFloat(e.target.value))}
              className="w-full mb-1"
            />
          ))}
          <div className="text-white/50 text-xs">[{params.scale.join(", ")}]</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Position</label>
          {params.position.map((val, i) => (
            <input
              key={`position-${i}`}
              type="range"
              min="-3"
              max="3"
              step="0.1"
              value={val}
              onChange={(e) => updateParam("position", i, Number.parseFloat(e.target.value))}
              className="w-full mb-1"
            />
          ))}
          <div className="text-white/50 text-xs">[{params.position.map((v) => v.toFixed(1)).join(", ")}]</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Rotation</label>
          {params.rotation.map((val, i) => (
            <input
              key={`rotation-${i}`}
              type="range"
              min={-Math.PI * 2}
              max={Math.PI * 2}
              step="0.1"
              value={val}
              onChange={(e) => updateParam("rotation", i, Number.parseFloat(e.target.value))}
              className="w-full mb-1"
            />
          ))}
          <div className="text-white/50 text-xs">[{params.rotation.map((v) => v.toFixed(1)).join(", ")}]</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Camera Position</label>
          {params.cameraPosition.map((val, i) => (
            <input
              key={`camera-${i}`}
              type="range"
              min="-10"
              max="10"
              step="0.1"
              value={val}
              onChange={(e) => updateParam("cameraPosition", i, Number.parseFloat(e.target.value))}
              className="w-full mb-1"
            />
          ))}
          <div className="text-white/50 text-xs">[{params.cameraPosition.map((v) => v.toFixed(1)).join(", ")}]</div>
        </div>

        <div>
          <label className="text-white/70 block mb-1">Camera FOV</label>
          <input
            type="range"
            min="20"
            max="100"
            step="1"
            value={params.cameraFov || 36}
            onChange={(e) => setParams((prev) => ({ ...prev, cameraFov: Number.parseInt(e.target.value) }))}
            className="w-full mb-1"
          />
          <div className="text-white/50 text-xs">{params.cameraFov || 36}</div>
        </div>
      </div>
    </div>
  )
}
