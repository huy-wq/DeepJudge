import ElementPlus from 'element-plus'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { setErrorNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import ReviewQueueView from '../src/views/ReviewQueueView.vue'
import { jsonResponse, stubFetch, type FetchCall } from './helpers'

enableAutoUnmount(afterEach)

const ITEMS = [
  {
    aggregate_id: 11,
    answer_id: 1,
    pseudo_id: 'S-0001',
    question_code: 'Q-01',
    raw_score: 6,
    final_score: null,
    max_score: 10,
    route: 'human',
    risk: 4.5,
    confidence: 0.6,
    dispersion: 0.4,
    total_range: 1,
    grounding_rate: 0.5,
    reasons: ['存在未接地判定（接地率 0.50）', '置信度 0.60 低于阈值 0.75'],
    created_at: '2026-10-28T00:00:00',
    reviewed: false,
  },
]

function handler(call: FetchCall): Response {
  if (call.path === '/api/auth/session') {
    return jsonResponse({ authenticated: true, expires_at: 1 })
  }
  if (call.path.startsWith('/api/results?')) {
    return jsonResponse({
      route: 'human',
      pending: true,
      total: ITEMS.length,
      limit: 100,
      offset: 0,
      items: ITEMS,
    })
  }
  return jsonResponse({ detail: `unexpected ${call.path}` }, 500)
}

async function mountQueue(path = '/review') {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const wrapper = mount(ReviewQueueView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  resetSessionForTests()
  setErrorNotifier(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('复核队列', () => {
  it('按风险列出待复核答卷与原因', async () => {
    stubFetch(handler)
    const { wrapper } = await mountQueue()

    expect(wrapper.find('[data-test="review-table"]').text()).toContain('S-0001')
    expect(wrapper.find('[data-test="review-table"]').text()).toContain('4.5')
    expect(wrapper.find('[data-test="review-table"]').text()).toContain('置信度 0.60')
    expect(wrapper.find('[data-test="review-total"]').text()).toContain('1')
  })

  it('从任务页带来的题号会作为筛选条件', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountQueue('/review?question_code=Q-01')

    const query = calls.find((call) => call.path.startsWith('/api/results'))
    expect(query?.path).toContain('question_code=Q-01')
    expect(wrapper.find('[data-test="review-filter"]').text()).toContain('Q-01')
  })

  it('加载失败时显示可重试的提示', async () => {
    stubFetch((call) =>
      call.path.startsWith('/api/results?')
        ? jsonResponse({ detail: '服务暂时不可用' }, 503)
        : handler(call),
    )
    const { wrapper } = await mountQueue()

    expect(wrapper.find('[data-test="review-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="review-empty"]').exists()).toBe(false)
  })

  it('队列为空时显示空状态', async () => {
    stubFetch((call) =>
      call.path.startsWith('/api/results?')
        ? jsonResponse({
            route: 'human',
            pending: true,
            total: 0,
            limit: 100,
            offset: 0,
            items: [],
          })
        : handler(call),
    )
    const { wrapper } = await mountQueue()

    expect(wrapper.find('[data-test="review-empty"]').exists()).toBe(true)
  })
})
