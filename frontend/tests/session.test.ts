import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { jsonResponse, emptyResponse, stubFetch } from './helpers'
import { request } from '../src/api/client'
import {
  consumeSessionError,
  ensureSession,
  markUnauthenticated,
  resetSessionForTests,
  session,
  signIn,
  signOut,
} from '../src/session'

beforeEach(() => {
  resetSessionForTests()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ensureSession', () => {
  it('首次调用向服务端确认，之后复用结果', async () => {
    const calls = stubFetch(() => jsonResponse({ authenticated: true, expires_at: 99 }))

    await expect(ensureSession()).resolves.toBe(true)
    await expect(ensureSession()).resolves.toBe(true)

    expect(calls.filter((call) => call.path === '/api/auth/session')).toHaveLength(1)
    expect(session.status).toBe('authenticated')
  })

  it('未登录时返回 false，不当作错误', async () => {
    stubFetch(() => jsonResponse({ detail: '未登录或会话已失效' }, 401))

    await expect(ensureSession()).resolves.toBe(false)
    expect(session.status).toBe('anonymous')
    expect(consumeSessionError()).toBeNull()
  })

  it('会话响应缺字段时给可读错误，而不是让页面在 undefined 上崩掉', async () => {
    stubFetch(() => jsonResponse({}))

    await expect(ensureSession()).resolves.toBe(false)
    expect(consumeSessionError()).toContain('响应格式不正确')
  })

  it('网络失败时记录可读错误，供路由守卫提示一次', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(ensureSession()).resolves.toBe(false)
    expect(consumeSessionError()).toContain('无法连接服务器')
    expect(consumeSessionError()).toBeNull()
  })
})

describe('signIn / signOut', () => {
  it('登录成功后进入已登录状态', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/login' ? emptyResponse() : jsonResponse({ authenticated: true }),
    )

    await signIn('let-me-in')

    expect(session.status).toBe('authenticated')
  })

  it('退出成功后回到未登录状态', async () => {
    stubFetch(() => emptyResponse())
    await signIn('let-me-in')

    await signOut()

    expect(session.status).toBe('anonymous')
  })

  it('退出失败时保留登录态，不假装已经退出', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/logout'
        ? jsonResponse({ detail: '服务暂时不可用' }, 503)
        : emptyResponse(),
    )
    await signIn('let-me-in')

    await expect(signOut()).rejects.toMatchObject({ message: '服务暂时不可用' })
    expect(session.status).toBe('authenticated')
  })

  it('真实接线：任意请求 401 后本地状态立刻变为未登录', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/session'
        ? jsonResponse({ authenticated: true, expires_at: 1 })
        : jsonResponse({ detail: '未登录或会话已失效' }, 401),
    )
    await ensureSession()

    await expect(request('/api/anything')).rejects.toMatchObject({ status: 401 })

    expect(session.status).toBe('anonymous')
  })

  it('手动标记未登录（退出/测试）', async () => {
    stubFetch(() => jsonResponse({ authenticated: true }))
    await ensureSession()
    expect(session.status).toBe('authenticated')

    markUnauthenticated()

    expect(session.status).toBe('anonymous')
  })
})
