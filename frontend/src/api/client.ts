/**
 * HTTP 客户端：统一把后端的 `{detail, code, issues}` 变成可读的 ApiError。
 *
 * 三个约定：
 * - 401 一律视为"会话已失效"：先清本地登录态，再由调用方跳登录页；
 * - 网络不通与"后端返回错误"分开报，前者最可能的原因是后端没启动；
 * - 后端没给 detail 时也必须有中文兜底，页面不允许出现空白或 `[object Object]`。
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(message: string, status: number, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** 把任意异常整理成一句可读中文；页面与全局提示共用同一套映射。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  // 路由守卫等地方传进来的是已经整理过的字符串，不能再降级成通用文案
  if (typeof error === 'string' && error.trim()) {
    return error
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return '操作失败，请稍后重试'
}

type UnauthorizedHandler = () => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/** 由会话模块注册；客户端本身不认识路由与 store，避免循环依赖。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return undefined
  }
  const text = await response.text()
  if (!text) {
    return undefined
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

function readableDetail(body: unknown): string | null {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
  }
  return null
}

function errorCode(body: unknown): string | null {
  if (body && typeof body === 'object' && 'code' in body) {
    const code = (body as { code?: unknown }).code
    if (typeof code === 'string' && code) {
      return code
    }
  }
  return null
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...init,
      headers: {
        ...(init.body ? { 'content-type': 'application/json' } : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError(
      '无法连接服务器，请确认后端已启动（默认 http://127.0.0.1:8000）',
      0,
      'NETWORK_ERROR',
    )
  }

  const body = await readBody(response)

  if (response.status === 401) {
    unauthorizedHandler?.()
    throw new ApiError(
      readableDetail(body) ?? '登录已过期，请重新登录',
      401,
      errorCode(body) ?? 'UNAUTHORIZED',
    )
  }

  if (!response.ok) {
    throw new ApiError(
      readableDetail(body) ?? '请求失败，请稍后重试',
      response.status,
      errorCode(body),
    )
  }

  return body as T
}
