import ElementPlus from 'element-plus'
import { createMemoryHistory } from 'vue-router'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setErrorNotifier } from '../src/feedback'
import LoginView from '../src/views/LoginView.vue'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import { emptyResponse, jsonResponse, stubFetch } from './helpers'

async function mountLogin() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/login')
  await router.isReady()
  const wrapper = mount(LoginView, { global: { plugins: [router, ElementPlus] } })
  return { wrapper, router }
}

beforeEach(() => {
  resetSessionForTests()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setErrorNotifier(null)
})

describe('登录页', () => {
  it('口令正确后进入首页', async () => {
    const calls = stubFetch((call) =>
      call.path === '/api/auth/login' ? emptyResponse() : jsonResponse({ authenticated: true }),
    )
    const { wrapper, router } = await mountLogin()

    await wrapper.find('input[type="password"]').setValue('let-me-in')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const loginCall = calls.find((call) => call.path === '/api/auth/login')
    expect(loginCall?.init.method).toBe('POST')
    expect(loginCall?.init.body).toBe(JSON.stringify({ password: 'let-me-in' }))

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('home'))
  })

  it('口令错误时留在登录页并给出可读提示', async () => {
    const toasts: string[] = []
    setErrorNotifier((message) => toasts.push(message))
    stubFetch(() => jsonResponse({ detail: '口令错误', code: 'INVALID_PASSWORD' }, 401))
    const { wrapper, router } = await mountLogin()

    await wrapper.find('input[type="password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[data-test="error"]').text()).toContain('口令错误')
    expect(router.currentRoute.value.name).toBe('login')
    // 只贴在表单旁边，不再额外弹一次 toast
    expect(toasts).toEqual([])
  })

  it('口令为空时不发请求，直接提示', async () => {
    const calls = stubFetch(() => emptyResponse())
    const { wrapper } = await mountLogin()

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[data-test="error"]').text()).toContain('请输入管理口令')
    expect(calls.some((call) => call.path === '/api/auth/login')).toBe(false)
  })

  it('刷新后带着已登录 cookie 访问登录页会直接回首页', async () => {
    stubFetch(() => jsonResponse({ authenticated: true, expires_at: 1 }))
    const router = createAppRouter(createMemoryHistory())

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('home')
  })
})
