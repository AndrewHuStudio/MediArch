import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { resolveDataProcessApiTarget } from './src/config/apiTarget'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const dataProcessApiTarget = resolveDataProcessApiTarget(env.VITE_DATA_PROCESS_API_TARGET)

  return {
    plugins: [react()],
    resolve: {
      alias: { '@': path.resolve(__dirname, './src') },
    },
    server: {
      port: 3011,
      proxy: {
        '/data-process': {
          target: dataProcessApiTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
