import * as THREE from "three"

export const debugUVMapping = (mesh: any) => {
  const uvAttr = mesh.geometry.attributes.uv
  if (!uvAttr) {
    console.log("No UV coordinates found on Object_2")
    return
  }

  let minU = Number.POSITIVE_INFINITY,
    maxU = Number.NEGATIVE_INFINITY,
    minV = Number.POSITIVE_INFINITY,
    maxV = Number.NEGATIVE_INFINITY

  for (let i = 0; i < uvAttr.count; i++) {
    const u = uvAttr.getX(i)
    const v = uvAttr.getY(i)
    minU = Math.min(minU, u)
    maxU = Math.max(maxU, u)
    minV = Math.min(minV, v)
    maxV = Math.max(maxV, v)
  }

  const uvWidth = maxU - minU
  const uvHeight = maxV - minV

  console.log("Cover UV extent:", uvWidth, ":", uvHeight)

  if (uvWidth > 0 && uvHeight > 0) {
    const aspectRatio = uvWidth / uvHeight
    console.log("UV-based texture aspect ratio:", aspectRatio)

    console.log("=== OPTIMAL TEXTURE DIMENSIONS (UV-BASED) ===")

    const baseSize = 1024
    let optimalWidth, optimalHeight

    if (aspectRatio > 1) {
      optimalWidth = baseSize * 2
      optimalHeight = Math.round(optimalWidth / aspectRatio)
      optimalHeight = Math.pow(2, Math.round(Math.log2(optimalHeight)))
    } else {
      optimalHeight = baseSize * 2
      optimalWidth = Math.round(optimalHeight * aspectRatio)
      optimalWidth = Math.pow(2, Math.round(Math.log2(optimalWidth)))
    }

    console.log("🎯 OPTIMAL SIZE FOR YOUR UV MAPPING:")
    console.log(`   ${optimalWidth} x ${optimalHeight} pixels`)
    console.log(`   Aspect ratio: ${aspectRatio.toFixed(3)} (${uvWidth.toFixed(3)} : ${uvHeight.toFixed(3)} UV units)`)

    console.log("📐 Alternative sizes:")
    console.log(`   High quality: ${optimalWidth * 2} x ${optimalHeight * 2}`)
    console.log(`   Standard: ${optimalWidth} x ${optimalHeight}`)
    console.log(`   Low quality: ${Math.max(512, optimalWidth / 2)} x ${Math.max(512, optimalHeight / 2)}`)

    console.log("💡 Your image should match this aspect ratio for best results")
    console.log("================================")
  } else {
    console.log("❌ Invalid UV mapping - width or height is zero")
    console.log("This object cannot display textures properly")
  }

  console.log("UV sample (first 20 coords):", uvAttr.array.slice(0, 20))
}

export const debugAllObjectsUV = (scene: any) => {
  console.log("=== UV Analysis for All Book Objects ===")

  if (!scene) {
    console.log("❌ Scene not available")
    return
  }

  const sketchfabModel = scene.getObjectByName("Sketchfab_model")
  if (!sketchfabModel) {
    console.log("❌ Sketchfab_model not found")
    return
  }

  console.log("✅ Found Sketchfab_model")

  const geode = sketchfabModel.getObjectByName("Geode")
  if (!geode) {
    console.log("❌ Geode not found")
    return
  }

  console.log("✅ Found Geode")

  const objectNames = ["Object_2", "Object_3", "Object_4"]

  console.log(
    "Children in Geode:",
    geode.children.map((child: any) => child.name),
  )

  objectNames.forEach((name) => {
    console.log(`\n--- Checking for ${name} ---`)
    const obj = geode.getObjectByName(name)

    if (!obj) {
      console.log(`❌ ${name} not found`)
      return
    }

    console.log(`✅ Found ${name}`)

    if (!(obj as any).geometry) {
      console.log(`❌ ${name} has no geometry`)
      return
    }

    const mesh = obj as any
    console.log(`--- ${name} UV Analysis ---`)
    console.log("Object name:", mesh.name)
    console.log("Material type:", mesh.material.constructor.name)

    const uvAttr = mesh.geometry.attributes.uv
    if (!uvAttr) {
      console.log("❌ No UV coordinates found")
    } else {
      let minU = Number.POSITIVE_INFINITY,
        maxU = Number.NEGATIVE_INFINITY,
        minV = Number.POSITIVE_INFINITY,
        maxV = Number.NEGATIVE_INFINITY

      for (let i = 0; i < uvAttr.count; i++) {
        const u = uvAttr.getX(i)
        const v = uvAttr.getY(i)
        minU = Math.min(minU, u)
        maxU = Math.max(maxU, u)
        minV = Math.min(minV, v)
        maxV = Math.max(maxV, v)
      }

      const uvWidth = maxU - minU
      const uvHeight = maxV - minV

      if (uvWidth === 0 && uvHeight === 0) {
        console.log("❌ UV coordinates exist but all are (0,0)")
      } else if (uvWidth > 0 && uvHeight > 0) {
        console.log("✅ Valid UV mapping found!")
        console.log("UV extent:", uvWidth, ":", uvHeight)
        console.log("Recommended texture aspect ratio:", uvWidth / uvHeight)
      } else {
        console.log("❌ Invalid UV mapping - some coordinates are zero")
      }

      console.log("UV sample (first 10 coords):", uvAttr.array.slice(0, 10))
    }
  })
}

export const generateUVForObject2 = (mesh: any | null) => {
  if (!mesh) {
    console.log("Object_2 not found, cannot generate UV coordinates")
    return
  }

  const geometry = mesh.geometry

  if (!geometry.attributes.position) {
    console.log("No position attribute found in geometry")
    return
  }

  console.log("Generating UV coordinates for front cover only...")

  const positions = geometry.attributes.position
  const uvArray = new Float32Array(positions.count * 2)

  geometry.computeBoundingBox()
  const bbox = geometry.boundingBox!

  let maxZ = bbox.min.z
  let minZ = bbox.max.z

  for (let i = 0; i < positions.count; i++) {
    const z = positions.getZ(i)
    maxZ = Math.max(maxZ, z)
    minZ = Math.min(minZ, z)
  }

  const zThreshold = minZ + (maxZ - minZ) * 0.8

  console.log(`Z range: ${minZ.toFixed(3)} to ${maxZ.toFixed(3)}, front threshold: ${zThreshold.toFixed(3)}`)

  const width = bbox.max.x - bbox.min.x
  const height = bbox.max.y - bbox.min.y

  for (let i = 0; i < positions.count; i++) {
    const x = positions.getX(i)
    const y = positions.getY(i)
    const z = positions.getZ(i)

    let u = 0,
      v = 0

    if (z >= zThreshold) {
      u = (x - bbox.min.x) / width
      v = (y - bbox.min.y) / height
    } else {
      u = 0
      v = 0
    }

    u = Math.max(0, Math.min(1, u))
    v = Math.max(0, Math.min(1, v))

    uvArray[i * 2] = u
    uvArray[i * 2 + 1] = v
  }

  geometry.setAttribute("uv", new THREE.BufferAttribute(uvArray, 2))
  geometry.attributes.uv.needsUpdate = true

  console.log("Front cover UV coordinates generated successfully!")
  console.log(`Generated ${positions.count} UV coordinates for front cover only`)

  debugUVMapping(mesh)
}

function splitCoverEdgeSeam(geometry: THREE.BufferGeometry, coverMask: Uint8Array) {
  const typedGeometry = geometry as any
  const pos = typedGeometry.attributes.position
  const uvOld = typedGeometry.attributes.uv!
  const index = typedGeometry.index!

  const posArr = []
  const uvArr = []
  const newIdx = []
  const vertMap = new Map<number, number>() // old → new index for edge copy

  // Copy all original vertices first to preserve geometry
  for (let i = 0; i < pos.count; i++) {
    posArr.push(pos.getX(i), pos.getY(i), pos.getZ(i))
    uvArr.push(0, 0) // will be overwritten later
  }

  for (let i = 0; i < index.count; i += 3) {
    const ia = index.getX(i)
    const ib = index.getX(i + 1)
    const ic = index.getX(i + 2)

    const aCover = coverMask[ia] === 1
    const bCover = coverMask[ib] === 1
    const cCover = coverMask[ic] === 1

    const faceIsCover = aCover && bCover && cCover
    const faceIsEdge = !aCover && !bCover && !cCover

    const ids = [ia, ib, ic]

    // Only duplicate vertices for mixed triangles
    const isMixed = !(faceIsCover || faceIsEdge)

    if (isMixed) {
      ids.forEach((id, k) => {
        const wantCoverUV = faceIsCover
        if (coverMask[id] !== +wantCoverUV) {
          // we need a duplicate
          let dup = vertMap.get(id)
          if (dup === undefined) {
            dup = posArr.length / 3 // next index
            vertMap.set(id, dup)
            posArr.push(pos.getX(id), pos.getY(id), pos.getZ(id))
            uvArr.push(0, 0) // will be overwritten later
          }
          ids[k] = dup
        }
      })
    }

    newIdx.push(...ids)
  }

  // build new geometry
  const g2 = new THREE.BufferGeometry()
  g2.setAttribute("position", new THREE.Float32BufferAttribute(posArr, 3))
  g2.setAttribute("uv", new THREE.Float32BufferAttribute(uvArr, 2))
  g2.setIndex(newIdx)

  return g2
}

export const generateUVForBothCovers = (mesh: any | null) => {
  if (!mesh) {
    console.log("Object_2 not found, cannot generate UV coordinates")
    return
  }

  const geometry = mesh.geometry

  if (!geometry.attributes.position) {
    console.log("No position attribute found in geometry")
    return
  }

  console.log("Generating UV coordinates using ultra-restrictive normals + spatial filtering...")

  // Compute vertex normals for robust front/back detection
  geometry.computeVertexNormals()

  const positions = geometry.attributes.position
  const normals = geometry.attributes.normal

  geometry.computeBoundingBox()
  const bbox = geometry.boundingBox!

  const FRONT_DOT = 0.98 // cos(11°) -> only faces looking almost perfectly +Z
  const BACK_DOT = -0.98 // -cos(11°) -> only faces looking almost perfectly -Z
  const MAX_XY_DEVIATION = 0.1 // Even stricter X/Y normal deviation allowed

  let maxZ = bbox.min.z
  let minZ = bbox.max.z

  for (let i = 0; i < positions.count; i++) {
    const z = positions.getZ(i)
    maxZ = Math.max(maxZ, z)
    minZ = Math.min(minZ, z)
  }

  const zRange = maxZ - minZ
  const frontZThreshold = maxZ - zRange * 0.05 // Only top 5% of Z range for front
  const backZThreshold = minZ + zRange * 0.05 // Only bottom 5% of Z range for back

  console.log(`Z range: ${minZ.toFixed(3)} to ${maxZ.toFixed(3)}`)
  console.log(`Front Z threshold: ${frontZThreshold.toFixed(3)}, Back Z threshold: ${backZThreshold.toFixed(3)}`)

  const width = bbox.max.x - bbox.min.x
  const height = bbox.max.y - bbox.min.y

  const coverMask = new Uint8Array(positions.count)
  let frontCount = 0,
    backCount = 0,
    spineCount = 0

  // First pass: determine which vertices belong to covers using both normals AND spatial position
  for (let i = 0; i < positions.count; i++) {
    const nx = normals.getX(i)
    const ny = normals.getY(i)
    const nz = normals.getZ(i)
    const z = positions.getZ(i)

    const hasGoodNormal = Math.abs(nx) < MAX_XY_DEVIATION && Math.abs(ny) < MAX_XY_DEVIATION
    const isStrictlyFront = nz >= FRONT_DOT && hasGoodNormal && z >= frontZThreshold
    const isStrictlyBack = nz <= BACK_DOT && hasGoodNormal && z <= backZThreshold

    if (isStrictlyFront || isStrictlyBack) {
      coverMask[i] = 1 // Cover vertex
      if (isStrictlyFront) frontCount++
      else backCount++
    } else {
      coverMask[i] = 0 // Edge/spine vertex
      spineCount++
    }
  }

  const newGeometry = splitCoverEdgeSeam(geometry, coverMask)

  // Apply the new geometry to the mesh
  mesh.geometry = newGeometry

  // Now generate UVs on the split geometry
  const newPositions = newGeometry.attributes.position
  const uvArray = new Float32Array(newPositions.count * 2)

  // Recompute normals for the new geometry
  newGeometry.computeVertexNormals()
  const newNormals = newGeometry.attributes.normal

  for (let i = 0; i < newPositions.count; i++) {
    const x = newPositions.getX(i)
    const y = newPositions.getY(i)
    const z = newPositions.getZ(i)
    const nx = newNormals.getX(i)
    const ny = newNormals.getY(i)
    const nz = newNormals.getZ(i)

    let u = -10,
      v = -10 // Default to far outside [0,1] range

    const hasGoodNormal = Math.abs(nx) < MAX_XY_DEVIATION && Math.abs(ny) < MAX_XY_DEVIATION
    const isStrictlyFront = nz >= FRONT_DOT && hasGoodNormal && z >= frontZThreshold
    const isStrictlyBack = nz <= BACK_DOT && hasGoodNormal && z <= backZThreshold

    if (isStrictlyFront) {
      // Front cover - only truly flat front faces in the right spatial location
      const normalizedU = (x - bbox.min.x) / width
      const normalizedV = (y - bbox.min.y) / height
      u = normalizedU * 0.5 // Left half of combined texture
      v = normalizedV
    } else if (isStrictlyBack) {
      // Back cover - only truly flat back faces in the right spatial location
      const normalizedU = (x - bbox.min.x) / width
      const normalizedV = (y - bbox.min.y) / height
      u = 0.5 + normalizedU * 0.5 // Right half of combined texture
      v = normalizedV
    }
    // All other vertices (spine, edges, curved areas) keep (-10, -10) for repeat wrapping

    uvArray[i * 2] = u
    uvArray[i * 2 + 1] = v
  }

  newGeometry.setAttribute("uv", new THREE.BufferAttribute(uvArray, 2))
  newGeometry.attributes.uv.needsUpdate = true

  console.log("Ultra-restrictive spatial + normal UV coordinates generated!")
  console.log(`Original vertices: ${positions.count}, New vertices: ${newPositions.count}`)
  console.log(`Front vertices: ${frontCount}, Back vertices: ${backCount}, Spine vertices: ${spineCount}`)

  debugUVMapping(mesh)
}
