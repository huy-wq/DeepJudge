import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { request } from '../src/api/client'
import { setErrorNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests, signOut } from '../src/session'
import { emptyResponse, jsonResponse, stubFetch } from './helpers'

function authenticatedFetch(): void {
  stubFetch((call) =>
    call.path === '/api/auth/session'
      ? jsonResponse({ authenticated: true, expires_at: 1 })
      : emptyResponse(),
  )
}

function anonymousFetch(): void {
  stubFetch(() => jsonResponse({ detail: '未登录或会话已失效' }, 401))
}

beforeEach(() => {
  resetSessionForTests()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setErrorNotifier(null)
})

describe('路由守卫', () => {
  it('未登录访问首页会跳转到登录页，并记住原地址', async () => {
    anonymousFetch()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('未登录访问不存在的管理路径也先去登录，而不是直接看到 404 页', async () => {
    anonymousFetch()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/no-such-page')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/no-such-page')
  })

  it('刷新（重新确认 cookie）后仍保持登录', async () => {
    authenticatedFetch()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('已登录访问登录页会被送回首页', async () => {
    authenticatedFetch()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('已登录访问不存在的路径才显示 404', async () => {
    authenticatedFetch()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/no-such-page')

    expect(router.currentRoute.value.name).toBe('not-found')
  })

  it('确认登录态失败时给出统一可读提示，而不是白屏', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const captured: string[] = []
    setErrorNotifier((message) => captured.push(message))
    const router = createAppRouter(createMemoryHistory())

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('login')
    expect(captured.join('')).toContain('无法连接服务器')
  })

  it('页面内任意请求 401 时立刻回到登录页（不等下一次导航）', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/session'
        ? jsonResponse({ authenticated: true, expires_at: 1 })
        : jsonResponse({ detail: '未登录或会话已失效' }, 401),
    )
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('home')

    await expect(request('/api/anything')).rejects.toMatchObject({ status: 401 })

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('懒加载 chunk 失败时进入可刷新页，而不是白屏', async () => {
    authenticatedFetch()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    router.addRoute({
      path: '/broken',
      name: 'broken',
      component: () => Promise.reject(new Error('chunk 加载失败')),
    })

    await router.push('/broken').catch(() => {})

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('load-error'))
  })

  it('退出后受保护页面不可访问', async () => {
    authenticatedFetch()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('home')

    await signOut()
    await router.push('/no-such-page')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/no-such-page')
  })
})
