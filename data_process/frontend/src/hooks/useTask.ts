import { useState, useCallback, useEffect, useRef } from 'react'
import { getTaskStatus } from '@/api/client'
import { useWebSocket } from './useWebSocket'
import { isTaskMissingError } from './taskError'

type TaskState = 'idle' | 'pending' | 'running' | 'completed' | 'failed'

interface TaskProgress {
  stage: string
  current: number
  total: number
  message: string
  extra?: Record<string, unknown>
}

interface UseTaskOptions {
  persistKey?: string
}

export function useTask(options: UseTaskOptions = {}) {
  const { persistKey } = options
  const [taskId, setTaskId] = useState<string | null>(() => {
    if (!persistKey || typeof window === 'undefined') return null
    return window.sessionStorage.getItem(persistKey)
  })
  const [state, setState] = useState<TaskState>('idle')
  const [progress, setProgress] = useState<TaskProgress | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [createdAt, setCreatedAt] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const { lastMessage } = useWebSocket(taskId)

  // WebSocket 进度更新
  useEffect(() => {
    if (!lastMessage) return
    setProgress({
      stage: lastMessage.stage,
      current: lastMessage.current,
      total: lastMessage.total,
      message: lastMessage.message,
      extra: lastMessage.extra,
    })
    if (lastMessage.stage === 'done') {
      const extra = lastMessage.extra as Record<string, unknown> | undefined
      if (extra?.error) {
        setState('failed')
        setError(String(extra.error))
      } else {
        setState('completed')
        setResult((extra?.result as Record<string, unknown>) ?? null)
      }
    }
  }, [lastMessage])

  // 轮询兜底 (WebSocket 断开时)
  useEffect(() => {
    if (!persistKey || typeof window === 'undefined') return
    if (taskId) window.sessionStorage.setItem(persistKey, taskId)
    else window.sessionStorage.removeItem(persistKey)
  }, [persistKey, taskId])

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    const syncTask = async () => {
      try {
        const status = await getTaskStatus(taskId)
        if (cancelled) return
        if (status.status === 'completed') {
          setState('completed')
          setResult(status.result)
          setError(null)
          setCreatedAt(status.created_at ?? null)
          return
        }
        if (status.status === 'failed') {
          setState('failed')
          setError(status.error)
          setCreatedAt(status.created_at ?? null)
          return
        }
        setState('running')
        setCreatedAt(status.created_at ?? null)
        if (status.progress) {
          const p = status.progress as Record<string, unknown>
          setProgress({
            stage: String(p.stage ?? ''),
            current: Number(p.current ?? 0),
            total: Number(p.total ?? 0),
            message: String(p.message ?? ''),
            extra: (p.extra as Record<string, unknown> | undefined) ?? undefined,
          })
        }
      } catch (error) {
        if (!cancelled) {
          if (isTaskMissingError(error)) {
            setTaskId(null)
            setState('idle')
            setProgress(null)
            setResult(null)
            setError(null)
            setCreatedAt(null)
          }
        }
      }
    }
    void syncTask()
    return () => { cancelled = true }
  }, [taskId])

  useEffect(() => {
    if (!taskId || state === 'completed' || state === 'failed') {
      if (pollRef.current) clearInterval(pollRef.current)
      return
    }
    pollRef.current = setInterval(async () => {
      try {
        const status = await getTaskStatus(taskId)
        if (status.status === 'completed') {
          setState('completed')
          setResult(status.result)
          setCreatedAt(status.created_at ?? null)
          if (pollRef.current) clearInterval(pollRef.current)
        } else if (status.status === 'failed') {
          setState('failed')
          setError(status.error)
          setCreatedAt(status.created_at ?? null)
          if (pollRef.current) clearInterval(pollRef.current)
        } else if (status.progress) {
          setCreatedAt(status.created_at ?? null)
          const p = status.progress as Record<string, unknown>
          setProgress({
            stage: String(p.stage ?? ''),
            current: Number(p.current ?? 0),
            total: Number(p.total ?? 0),
            message: String(p.message ?? ''),
            extra: (p.extra as Record<string, unknown> | undefined) ?? undefined,
          })
        }
      } catch (error) {
        if (isTaskMissingError(error)) {
          if (pollRef.current) clearInterval(pollRef.current)
          setTaskId(null)
          setState('idle')
          setProgress(null)
          setResult(null)
          setError(null)
          setCreatedAt(null)
        }
      }
    }, 3000)

    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [taskId, state])

  const start = useCallback(async (startFn: () => Promise<{ task_id: string }>) => {
    if (pollRef.current) clearInterval(pollRef.current)
    setTaskId(null)
    setState('pending')
    setProgress(null)
    setResult(null)
    setError(null)
    setCreatedAt(null)
    try {
      const resp = await startFn()
      setTaskId(resp.task_id)
      setState('running')
      setCreatedAt(new Date().toISOString())
    } catch (e) {
      setState('failed')
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const reset = useCallback(() => {
    setTaskId(null)
    setState('idle')
    setProgress(null)
    setResult(null)
    setError(null)
    setCreatedAt(null)
  }, [])

  return { taskId, state, progress, result, error, createdAt, start, reset }
}
