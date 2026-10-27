import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminLayout from '../src/layouts/AdminLayout.vue'
import { createAppRouter } from '../src/router'
import { resetSessionForTests, signIn } from '../src/session'
import { emptyResponse, jsonResponse, stubFetch } from './helpers'

beforeEach(() => {
  resetSessionForTests()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('管理端骨架', () => {
  it('退出后回到登录页', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/logout'
        ? emptyResponse()
        : jsonResponse({ authenticated: true, expires_at: 1 }),
    )
    const router = createAppRouter(createMemoryHistory())
    await signIn('let-me-in')
    await router.push('/')
    const wrapper = mount(AdminLayout, { global: { plugins: [router, ElementPlus] } })

    await wrapper.get('[data-test="logout"]').trigger('click')
    await flushPromises()

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
  })

  it('退出请求遇到 401 时也会回到登录页', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/logout'
        ? jsonResponse({ detail: '未登录或会话已失效' }, 401)
        : jsonResponse({ authenticated: true, expires_at: 1 }),
    )
    const router = createAppRouter(createMemoryHistory())
    await signIn('let-me-in')
    await router.push('/')
    const wrapper = mount(AdminLayout, { global: { plugins: [router, ElementPlus] } })

    await wrapper.get('[data-test="logout"]').trigger('click')
    await flushPromises()

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
  })

  it('页面渲染失败时错误卡只替换内容区，导航还在', async () => {
    const Boom = defineComponent({
      render() {
        throw new Error('页面炸了')
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          component: AdminLayout,
          children: [{ path: '', component: Boom }],
        },
      ],
    })
    await router.push('/')
    const wrapper = mount(AdminLayout, { global: { plugins: [router, ElementPlus] } })

    await nextTick()

    expect(wrapper.text()).toContain('页面出错了')
    expect(wrapper.find('[data-test="logout"]').exists()).toBe(true)
  })

  it('退出失败时提示错误并留在管理端', async () => {
    stubFetch((call) =>
      call.path === '/api/auth/logout'
        ? jsonResponse({ detail: '服务暂时不可用' }, 503)
        : jsonResponse({ authenticated: true, expires_at: 1 }),
    )
    const router = createAppRouter(createMemoryHistory())
    await signIn('let-me-in')
    await router.push('/')
    const wrapper = mount(AdminLayout, { global: { plugins: [router, ElementPlus] } })

    await wrapper.get('[data-test="logout"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('home')
  })
})
