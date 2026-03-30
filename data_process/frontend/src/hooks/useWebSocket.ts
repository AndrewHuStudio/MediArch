import { useEffect, useRef, useState } from 'react'

interface ProgressMessage {
  task_id: string
  module: string
  stage: string
  current: number
  total: number
  message: string
  extra?: Record<string, unknown>
}

export function useWebSocket(taskId: string | null) {
  const [lastMessage, setLastMessage] = useState<ProgressMessage | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!taskId) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/data-process/ws/progress/${taskId}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (!data.heartbeat) {
          setLastMessage(data as ProgressMessage)
        }
      } catch { /* ignore */ }
    }

    // 心跳
    const interval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send('ping')
      }
    }, 25000)

    return () => {
      clearInterval(interval)
      ws.close()
      wsRef.current = null
      setConnected(false)
    }
  }, [taskId])

  return { lastMessage, connected }
}
