import ElementPlus from 'element-plus'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { setErrorNotifier, setSuccessNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import type { ResultDetail } from '../src/api/results'
import ReviewDetailView from '../src/views/ReviewDetailView.vue'
import { DETAIL } from './review-fixture'
import { jsonResponse, stubFetch, type FetchCall } from './helpers'

enableAutoUnmount(afterEach)

let detail = DETAIL as unknown as ResultDetail

function handler(call: FetchCall): Response {
  const method = (call.init.method ?? 'GET').toUpperCase()
  if (call.path === '/api/auth/session') {
    return jsonResponse({ authenticated: true, expires_at: 1 })
  }
  if (call.path === '/api/results/11' && method === 'GET') {
    return jsonResponse(detail)
  }
  if (call.path === '/api/results/11/review' && method === 'POST') {
    const payload = JSON.parse(call.init.body as string) as {
      decision: 'accept' | 'override'
      final_score?: number
      comment?: string
    }
    return jsonResponse({
      review_id: 1,
      aggregate_id: 11,
      reviewer_id: 'admin',
      final_score: payload.final_score ?? DETAIL.score.raw,
      decision: payload.decision,
      comment: payload.comment ?? '',
      created_at: '2026-10-28T00:00:00',
    })
  }
  return jsonResponse({ detail: `unexpected ${method} ${call.path}` }, 500)
}

async function mountDetail(path = '/review/11') {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const wrapper = mount(ReviewDetailView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return { wrapper, router }
}

let successes: string[] = []

beforeEach(() => {
  resetSessionForTests()
  detail = DETAIL as unknown as ResultDetail
  successes = []
  setErrorNotifier(() => {})
  setSuccessNotifier((message) => successes.push(message))
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSuccessNotifier(null)
})

describe('复核详情', () => {
  it('点击证据在原文中高亮对应片段', async () => {
    stubFetch(handler)
    const { wrapper } = await mountDetail()

    expect(wrapper.find('[data-test="answer-text"]').text()).toContain('客户端发送 SYN')
    expect(wrapper.find('[data-test="evidence-mark"]').exists()).toBe(false)

    await wrapper.findAll('[data-test="show-evidence"]')[0].trigger('click')

    const mark = wrapper.find('[data-test="evidence-mark"]')
    expect(mark.exists()).toBe(true)
    expect(mark.text()).toBe('客户端发送 SYN')
    // 高亮是把原文切开，不是替换：前后文仍在
    expect(wrapper.find('[data-test="answer-text"]').text()).toContain('服务器回复 SYN+ACK')
  })

  it('改分必须填写意见，提交后状态变为已定稿', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountDetail()

    const score = wrapper.find('[data-test="final-score"] input')
    await score.setValue('4.5')
    await wrapper.find('[data-test="review-comment"]').setValue('P2 的证据只覆盖半个要点')
    await wrapper.find('[data-test="override-score"]').trigger('click')
    await flushPromises()

    const submitted = calls.find((call) => call.path === '/api/results/11/review')
    expect(JSON.parse(submitted?.init.body as string)).toEqual({
      decision: 'override',
      final_score: 4.5,
      comment: 'P2 的证据只覆盖半个要点',
    })
    expect(successes.join(' ')).toContain('已定稿')
    expect(wrapper.find('[data-test="finalized"]').text()).toContain('4.5')
    expect(wrapper.find('[data-test="override-score"]').exists()).toBe(false)
  })

  it('没有意见时不能提交改分', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountDetail()

    expect(wrapper.find('[data-test="override-score"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="override-hint"]').text()).toContain('必须填写意见')

    await wrapper.find('[data-test="override-score"]').trigger('click')
    await flushPromises()

    expect(calls.some((call) => call.path === '/api/results/11/review')).toBe(false)
  })

  it('采纳原分不需要意见，定稿分等于模型分', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-test="accept-score"]').trigger('click')
    await flushPromises()

    const submitted = calls.find((call) => call.path === '/api/results/11/review')
    expect(JSON.parse(submitted?.init.body as string)).toEqual({
      decision: 'accept',
      comment: '',
    })
    expect(wrapper.find('[data-test="finalized"]').text()).toContain('5.5')
  })

  it('已被重评取代的结论不能定稿，并给出当前结论入口', async () => {
    detail = { ...DETAIL, current: false, superseded_by: 12 } as unknown as ResultDetail
    stubFetch(handler)
    const { wrapper } = await mountDetail()

    expect(wrapper.find('[data-test="superseded"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="superseded"]').text()).toContain('12')
    expect(wrapper.find('[data-test="override-score"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="current-result-link"]').attributes('href')).toBe(
      '/review/12',
    )
  })

  it('已定稿的结果只展示结论，不再给操作入口', async () => {
    detail = {
      ...DETAIL,
      score: { ...DETAIL.score, final: 6 },
      review: {
        review_id: 3,
        aggregate_id: 11,
        reviewer_id: 'admin',
        final_score: 6,
        decision: 'override',
        comment: '扣两分',
        created_at: '2026-10-28T00:00:00',
      },
    } as unknown as ResultDetail
    stubFetch(handler)
    const { wrapper } = await mountDetail()

    expect(wrapper.find('[data-test="finalized"]').text()).toContain('6')
    expect(wrapper.find('[data-test="finalized"]').text()).toContain('扣两分')
    expect(wrapper.find('[data-test="override-score"]').exists()).toBe(false)
  })
})
