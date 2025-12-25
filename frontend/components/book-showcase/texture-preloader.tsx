export class TexturePreloader {
  private static instance: TexturePreloader
  private preloadedTextures = new Map<string, HTMLImageElement>()
  private loadingPromises = new Map<string, Promise<HTMLImageElement>>()

  static getInstance(): TexturePreloader {
    if (!TexturePreloader.instance) {
      TexturePreloader.instance = new TexturePreloader()
    }
    return TexturePreloader.instance
  }

  async preloadImage(url: string): Promise<HTMLImageElement> {
    // Return cached image if already loaded
    if (this.preloadedTextures.has(url)) {
      return this.preloadedTextures.get(url)!
    }

    // Return existing promise if already loading
    if (this.loadingPromises.has(url)) {
      return this.loadingPromises.get(url)!
    }

    // Create new loading promise
    const loadPromise = new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.crossOrigin = "anonymous"

      img.onload = () => {
        this.preloadedTextures.set(url, img)
        this.loadingPromises.delete(url)
        resolve(img)
      }

      img.onerror = () => {
        this.loadingPromises.delete(url)
        reject(new Error(`Failed to preload image: ${url}`))
      }

      img.src = url
    })

    this.loadingPromises.set(url, loadPromise)
    return loadPromise
  }

  async preloadAllBookTextures(): Promise<void> {
    const texturePaths = [
      "/images/standard-front.png",
      "/images/standard-back.png",
      "/images/policy-front.png",
      "/images/policy-back.png",
      "/images/book-front.png",
      "/images/book-back.png",
      "/images/paper-front.png",
      "/images/paper-back.png",
      "/images/online-cases-front.png",
      "/images/online-cases-back.png",
    ]

    try {
      await Promise.all(texturePaths.map((path) => this.preloadImage(path)))
      console.log("All book textures preloaded successfully")
    } catch (error) {
      console.warn("Some textures failed to preload:", error)
    }
  }

  getPreloadedImage(url: string): HTMLImageElement | null {
    return this.preloadedTextures.get(url) || null
  }
}
