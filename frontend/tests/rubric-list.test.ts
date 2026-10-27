import ElementPlus from 'element-plus'
import { flushPromises, enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { setErrorNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import RubricListView from '../src/views/RubricListView.vue'
import { RECORD } from './rubric-fixture'
import { jsonResponse, stubFetch, type FetchCall } from './helpers'

enableAutoUnmount(afterEach)

const ITEMS = [
  {
    rubric_id: 7,
    question_code: 'Q-01',
    version: 2,
    stem: '简述 TCP 三次握手',
    total_score: 10,
    item_count: 3,
    created_at: '2026-10-27T00:00:00',
  },
  {
    rubric_id: 8,
    question_code: 'Q-02',
    version: 1,
    stem: '解释拥塞控制',
    total_score: 8,
    item_count: 2,
    created_at: '2026-10-28T00:00:00',
  },
]

function handler(call: FetchCall): Response {
  if (call.path === '/api/auth/session') {
    return jsonResponse({ authenticated: true, expires_at: 1 })
  }
  if (call.path === '/api/rubrics') {
    return jsonResponse({ total: ITEMS.length, items: ITEMS })
  }
  if (call.path === '/api/rubrics/7') {
    return jsonResponse(RECORD)
  }
  return jsonResponse({ detail: `unexpected ${call.path}` }, 500)
}

async function mountList() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/rubrics')
  await router.isReady()
  const wrapper = mount(RubricListView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  resetSessionForTests()
  setErrorNotifier(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  setErrorNotifier(null)
})

describe('评分标准列表', () => {
  it('列出已保存的标准，并可打开某一版', async () => {
    stubFetch(handler)
    const { wrapper, router } = await mountList()

    expect(wrapper.text()).toContain('Q-01')
    expect(wrapper.text()).toContain('简述 TCP 三次握手')

    await wrapper.findAll('[data-test="open-rubric"]')[0].trigger('click')

    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/rubrics/7'))
  })

  it('新建按钮进入编辑页', async () => {
    stubFetch(handler)
    const { wrapper, router } = await mountList()

    await wrapper.find('[data-test="new-rubric"]').trigger('click')

    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/rubrics/new'))
  })

  it('没有标准时显示空状态', async () => {
    stubFetch((call) =>
      call.path === '/api/rubrics'
        ? jsonResponse({ total: 0, items: [] })
        : handler(call),
    )
    const { wrapper } = await mountList()

    expect(wrapper.find('[data-test="empty"]').exists()).toBe(true)
  })
})
