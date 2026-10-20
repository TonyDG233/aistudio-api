import { getToken } from './client.ts'

export class LogStreamError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'LogStreamError'
    this.status = status
  }
}

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface LogEvent {
  id: number
  ts: string
  level: LogLevel | string
  logger: string
  message: string
}

export interface LogStreamOptions {
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  lastId?: number | null
  signal: AbortSignal
}

export async function openLogStream(options: LogStreamOptions): Promise<Response> {
  const query = new URLSearchParams({ level: options.level })
  if (options.lastId) query.set('last_id', String(options.lastId))
  const token = getToken()
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`/logs/stream?${query.toString()}`, {
    headers,
    signal: options.signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new LogStreamError(response.status, `日志流连接失败 (${response.status})`)
  }
  return response
}

export interface LogExportOptions {
  start?: string
  end?: string
  days?: number
}

export async function downloadLogArchive(options: LogExportOptions): Promise<void> {
  const query = new URLSearchParams()
  if (options.days) query.set('days', String(options.days))
  if (options.start) query.set('start', options.start)
  if (options.end) query.set('end', options.end)
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`/logs/export?${query.toString()}`, {
    headers,
  })
  if (!response.ok) {
    let detail = `导出失败 (${response.status})`
    try {
      const payload = await response.json()
      if (typeof payload?.detail === 'string') detail = payload.detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }

  const disposition = response.headers.get('Content-Disposition') || ''
  const match = /filename="([^"]+)"/.exec(disposition)
  const fallback = options.days && options.days > 1
    ? `aistudio-logs-${new Date().toISOString().slice(0, 10)}.zip`
    : `aistudio-logs-${new Date().toISOString().slice(0, 10)}.log`
  const filename = match?.[1] || fallback

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
