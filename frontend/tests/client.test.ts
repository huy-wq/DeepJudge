import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request, setUnauthorizedHandler } from '../src/api/client'
import { emptyResponse, jsonResponse, stubFetch } from './helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
})

describe('request', () => {
  it('解析 JSON 并返回给调用方', async () => {
    stubFetch(() => jsonResponse({ authenticated: true, expires_at: 123 }))

    await expect(request('/api/auth/session')).resolves.toEqual({
      authenticated: true,
      expires_at: 123,
    })
  })

  it('204 没有响应体时返回 undefined', async () => {
    stubFetch(() => emptyResponse())

    await expect(request('/api/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })

  it('把后端的 detail 原样作为可读错误', async () => {
    stubFetch(() => jsonResponse({ detail: '口令错误', code: 'INVALID_PASSWORD' }, 401))

    await expect(request('/api/auth/login', { method: 'POST' })).rejects.toMatchObject({
      message: '口令错误',
      status: 401,
      code: 'INVALID_PASSWORD',
    })
  })

  it('5xx 没有 detail 时给中文兜底，不出现空消息', async () => {
    stubFetch(() => new Response('oops', { status: 500 }))

    const error = await request('/api/anything').catch((failure: unknown) => failure)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe('请求失败，请稍后重试')
    expect((error as ApiError).status).toBe(500)
  })

  it('连不上后端时明确指出可能的原因', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(request('/api/auth/session')).rejects.toMatchObject({
      message: expect.stringContaining('无法连接服务器'),
      code: 'NETWORK_ERROR',
    })
  })

  it('401 会通知会话层并清掉本地登录态', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    stubFetch(() => jsonResponse({ detail: '未登录或会话已失效' }, 401))

    await expect(request('/api/results')).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })
})
