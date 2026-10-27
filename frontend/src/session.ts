import { reactive, readonly } from 'vue'

import { fetchSession, login, logout } from './api/auth'
import { ApiError, errorMessage, setUnauthorizedHandler } from './api/client'

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous'

const state = reactive({
  status: 'unknown' as SessionStatus,
  /** 最近一次"确认登录态"失败的原因；路由守卫提示一次后清空。 */
  error: null as string | null,
})

let pending: Promise<boolean> | null = null
let unauthorizedNavigator: (() => void) | null = null

export const session = readonly(state)

function clear(): void {
  state.status = 'anonymous'
}

export function markUnauthenticated(): void {
  clear()
}

/**
 * 401 发生时由客户端回调这里：先清登录态，再让路由层把当前页送回登录页。
 * 导航动作由路由模块注册；session 不认识 router，也不认识页面。
 */
function handleUnauthorized(): void {
  clear()
  unauthorizedNavigator?.()
}

export function setUnauthorizedNavigator(navigator: (() => void) | null): void {
  unauthorizedNavigator = navigator
}

setUnauthorizedHandler(handleUnauthorized)

/**
 * 确认登录态，整个页面只请求一次。
 *
 * 刷新页面时 cookie 还在，所以重放 `GET /api/auth/session` 就能恢复登录——
 * 前端不自己存 token，也不信任 localStorage 里的任何"已登录"标记。
 */
export async function ensureSession(): Promise<boolean> {
  if (state.status === 'authenticated') {
    return true
  }
  if (state.status === 'anonymous') {
    return false
  }
  if (pending) {
    return pending
  }

  pending = (async () => {
    try {
      const info = await fetchSession()
      state.status = info.authenticated ? 'authenticated' : 'anonymous'
      state.error = null
      return state.status === 'authenticated'
    } catch (error) {
      clear()
      const unauthorized = error instanceof ApiError && error.status === 401
      // 401 是"没登录"，不该弹错；网络/5xx 要让教师看到原因，且下次导航还能重试
      state.status = unauthorized ? 'anonymous' : 'unknown'
      state.error = unauthorized ? null : errorMessage(error)
      return false
    } finally {
      pending = null
    }
  })()

  return pending
}

/** 路由守卫取一次错误提示，避免每次导航都重复弹同一条。 */
export function consumeSessionError(): string | null {
  const message = state.error
  state.error = null
  return message
}

export async function signIn(password: string): Promise<void> {
  await login(password)
  state.status = 'authenticated'
  state.error = null
}

export async function signOut(): Promise<void> {
  await logout()
  clear()
}

/** 测试专用：把模块级状态恢复到"未确认"，并重新挂上 401 处理。 */
export function resetSessionForTests(): void {
  state.status = 'unknown'
  state.error = null
  pending = null
  unauthorizedNavigator = null
  setUnauthorizedHandler(handleUnauthorized)
}
