import { ApiError, request } from './client'

export interface SessionInfo {
  authenticated: boolean
  expires_at: number
}

export async function fetchSession(): Promise<SessionInfo> {
  const info = await request<SessionInfo>('/api/auth/session')
  // 类型断言在运行时什么也不保证：后端 200 却给了空体时，宁可报一句可读错误
  if (!info || typeof info.authenticated !== 'boolean') {
    throw new ApiError('登录状态响应格式不正确，请刷新重试', 0, 'BAD_SESSION_RESPONSE')
  }
  return info
}

export function login(password: string): Promise<void> {
  return request<void>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
}

export function logout(): Promise<void> {
  return request<void>('/api/auth/logout', { method: 'POST' })
}
