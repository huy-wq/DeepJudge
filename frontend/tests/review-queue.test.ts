import ElementPlus from 'element-plus'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { setErrorNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import ReviewQueueView from '../src/views/ReviewQueueView.vue'
import { AUTO_ROW, DETAIL, HUMAN_ROW } from './review-fixture'
import { jsonResponse, stubFetch, type FetchCall } from './helpers'

enableAutoUnmount(afterEach)

function resultsFor(call: FetchCall): Response {
  const url = new URL(call.path, 'http://localhost')
  const route = url.searchParams.get('route') ?? 'human'
  const items = route === 'auto' ? [AUTO_ROW] : [HUMAN_ROW]
  return jsonResponse({
    route,
    pending: true,
    total: items.length,
    limit: 100,
    offset: 0,
    items,
  })
}

function handler(call: FetchCall): Response {
  if (call.path === '/api/auth/session') {
    return jsonResponse({ authenticated: true, expires_at: 1 })
  }
  if (call.path.startsWith('/api/results?')) {
    return resultsFor(call)
  }
  if (call.path === '/api/results/11' && (call.init.method ?? 'GET') === 'GET') {
    return jsonResponse(DETAIL)
  }
  if (call.path === '/api/results/batch-accept' && call.init.method === 'POST') {
    const ids = JSON.parse(call.init.body as string).aggregate_ids as number[]
    return jsonResponse({
      reviews: ids.map((id, index) => ({
        review_id: index + 1,
        aggregate_id: id,
        reviewer_id: 'admin',
        final_score: 8,
        decision: 'accept',
        comment: '',
        created_at: '2026-10-28T00:00:00',
      })),
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
    expect(wrapper.find('[data-test="review-table"]').text()).toContain('4.50')
    expect(wrapper.find('[data-test="review-table"]').text()).toContain('置信度 0.60')
    expect(wrapper.find('[data-test="review-total"]').text()).toContain('1')
  })

  it('从任务页带来的题号会作为筛选条件', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountQueue('/review?question_code=Q-01')

    const query = calls.find((call) => call.path.startsWith('/api/results?'))
    expect(query?.path).toContain('question_code=Q-01')
    expect(wrapper.find('[data-test="review-filter"]').text()).toContain('Q-01')
  })

  it('切到自动通过分页会按 route=auto 重新取队列', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountQueue()

    await wrapper.find('[data-test="tab-auto"]').trigger('click')
    await flushPromises()

    const queries = calls.filter((call) => call.path.startsWith('/api/results?'))
    expect(queries[queries.length - 1].path).toContain('route=auto')
    expect(wrapper.find('[data-test="review-table"]').text()).toContain('S-0002')
  })

  it('批量通过前弹确认，确认后才提交', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountQueue()

    await wrapper.find('[data-test="tab-auto"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-test="pick-result"] input').setValue(true)
    await wrapper.find('[data-test="batch-accept"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('确认通过这批结果')
    expect(calls.some((call) => call.path === '/api/results/batch-accept')).toBe(false)

    await wrapper.find('[data-test="batch-confirm"]').trigger('click')
    await flushPromises()

    const submitted = calls.find((call) => call.path === '/api/results/batch-accept')
    expect(JSON.parse(submitted?.init.body as string)).toEqual({
      aggregate_ids: [12],
      comment: '',
    })
  })

  it('取消批量通过不会发请求', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountQueue()

    await wrapper.find('[data-test="tab-auto"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-test="pick-result"] input').setValue(true)
    await wrapper.find('[data-test="batch-accept"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-test="batch-cancel"]').trigger('click')
    await flushPromises()

    expect(calls.some((call) => call.path === '/api/results/batch-accept')).toBe(false)
  })

  it('打开某份结果进入详情页', async () => {
    stubFetch(handler)
    const { wrapper, router } = await mountQueue()

    await wrapper.find('[data-test="open-result"]').trigger('click')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('review-detail'))
    expect(router.currentRoute.value.params.id).toBe('11')
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
