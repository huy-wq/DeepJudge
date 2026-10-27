import ElementPlus from 'element-plus'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { setErrorNotifier, setSuccessNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import GradingView from '../src/views/GradingView.vue'
import { jsonResponse, stubFetch, type FetchCall } from './helpers'

enableAutoUnmount(afterEach)

const RUBRICS = {
  total: 1,
  items: [
    {
      rubric_id: 2,
      question_code: 'Q-01',
      version: 1,
      stem: '简述 TCP 三次握手',
      total_score: 10,
      item_count: 3,
      created_at: '2026-10-27T00:00:00',
    },
  ],
}

const ANSWERS = {
  question_code: 'Q-01',
  total: 2,
  items: [
    {
      answer_id: 1,
      pseudo_id: 'S-0001',
      source: 'csv',
      created_at: '2026-10-28T00:00:00',
      length: 18,
    },
    {
      answer_id: 2,
      pseudo_id: 'S-0002',
      source: 'csv',
      created_at: '2026-10-28T00:00:01',
      length: 24,
    },
  ],
}

const PREVIEW = {
  ok: true,
  error: null,
  total_rows: 3,
  blank_lines: 0,
  rows_with_student_id: 2,
  question_codes: ['Q-01'],
  questions: [{ code: 'Q-01', exists: true, total_score: 10 }],
  rows: [
    {
      line: 2,
      question_code: 'Q-01',
      answer_text: '客户端发送 SYN，进入 SYN-SENT',
      content_hash: 'a',
      duplicate: false,
    },
    {
      line: 3,
      question_code: 'Q-01',
      answer_text: '客户端发送 SYN，进入 SYN-SENT',
      content_hash: 'a',
      duplicate: true,
    },
    {
      line: 4,
      question_code: 'Q-01',
      answer_text: '服务器回复 SYN+ACK',
      content_hash: 'b',
      duplicate: false,
    },
  ],
  problems: [{ line: 5, reason: '答案为空' }],
}

function taskPayload(overrides: Record<string, unknown> = {}) {
  return {
    task_id: 9,
    question_code: 'Q-01',
    rubric_id: 2,
    samples: 3,
    status: 'running',
    total: 2,
    done: 1,
    failed: 0,
    error: null,
    items: [
      {
        answer_id: 1,
        pseudo_id: 'S-0001',
        status: 'graded',
        error: null,
        aggregate_id: 11,
        current: true,
        score: 7,
        max_score: 10,
        route: 'auto',
        confidence: 1,
        final_score: null,
        state: 'graded',
      },
      {
        answer_id: 2,
        pseudo_id: 'S-0002',
        status: 'running',
        error: null,
        aggregate_id: null,
        current: true,
        score: null,
        max_score: null,
        route: null,
        confidence: null,
        final_score: null,
        state: 'running',
      },
    ],
    ...overrides,
  }
}

const DONE_WITH_FAILURE = taskPayload({
  status: 'done',
  done: 1,
  failed: 1,
  items: [
    {
      answer_id: 1,
      pseudo_id: 'S-0001',
      status: 'graded',
      error: null,
      aggregate_id: 11,
      current: true,
      score: 7,
      max_score: 10,
      route: 'human',
      confidence: 0.6,
      final_score: null,
      state: 'needs_review',
    },
    {
      answer_id: 2,
      pseudo_id: 'S-0002',
      status: 'failed',
      error: '内部错误：模拟模型故障',
      aggregate_id: null,
      current: true,
      score: null,
      max_score: null,
      route: null,
      confidence: null,
      final_score: null,
      state: 'failed',
    },
  ],
})

interface Scenario {
  rubrics?: unknown
  preview?: unknown
  imported?: unknown
  task?: () => unknown
}

function handlerFor(scenario: Scenario = {}) {
  return (call: FetchCall): Response => {
    const method = (call.init.method ?? 'GET').toUpperCase()
    if (call.path === '/api/auth/session') {
      return jsonResponse({ authenticated: true, expires_at: 1 })
    }
    if (call.path === '/api/rubrics' && method === 'GET') {
      return jsonResponse(scenario.rubrics ?? RUBRICS)
    }
    if (call.path === '/api/answers/preview' && method === 'POST') {
      return jsonResponse(
        scenario.preview ?? PREVIEW,
      )
    }
    if (call.path === '/api/answers/import' && method === 'POST') {
      return jsonResponse(
        scenario.imported ?? {
          imported: 2,
          skipped_duplicates: 1,
          pseudo_ids: ['S-0001', 'S-0002'],
          problems: [],
          error: null,
        },
      )
    }
    if (call.path.startsWith('/api/answers?') && method === 'GET') {
      return jsonResponse(ANSWERS)
    }
    if (call.path === '/api/tasks' && method === 'POST') {
      return jsonResponse(taskPayload())
    }
    if (call.path === '/api/tasks/9' && method === 'GET') {
      return jsonResponse(scenario.task ? scenario.task() : taskPayload())
    }
    if (call.path === '/api/tasks/9/retry' && method === 'POST') {
      return jsonResponse({ reset: 1, ...taskPayload() })
    }
    return jsonResponse({ detail: `unexpected ${method} ${call.path}` }, 500)
  }
}

async function mountGrading(path = '/grading') {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const wrapper = mount(GradingView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return { wrapper, router }
}

let successes: string[] = []

beforeEach(() => {
  resetSessionForTests()
  successes = []
  setErrorNotifier(() => {})
  setSuccessNotifier((message) => {
    successes.push(message)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSuccessNotifier(null)
})

describe('导入答卷', () => {
  it('预览显示行数、重复项与坏行，且不回显学号', async () => {
    stubFetch(handlerFor())
    const { wrapper } = await mountGrading()

    await wrapper.find('[data-test="import-text"]').setValue(
      '学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n2024002,Q-01,服务器回复 SYN+ACK\n',
    )
    await wrapper.find('[data-test="preview"]').trigger('click')
    await flushPromises()

    const summary = wrapper.find('[data-test="preview-summary"]').text()
    expect(summary).toContain('共 3 行')
    expect(summary).toContain('重复 1 行')
    expect(summary).toContain('坏行 1 行')
    expect(wrapper.text()).toContain('第 5 行：答案为空')
    expect(wrapper.find('[data-test="preview-table"]').text()).toContain('重复')
    // 学号只存在于教师粘贴的原文里，任何预览区域都不能出现
    expect(wrapper.find('[data-test="preview-table"]').text()).not.toContain('2024001')
    expect(wrapper.find('[data-test="preview-summary"]').text()).not.toContain('2024001')
    expect(wrapper.find('[data-test="preview-problems"]').text()).not.toContain('2024001')
  })

  it('选中 CSV 文件后直接给出预览，不用再点一次', async () => {
    const calls = stubFetch(handlerFor())
    const { wrapper } = await mountGrading()

    const file = new File(
      ['学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n'],
      'answers.csv',
      { type: 'text/csv' },
    )
    const input = wrapper.find('[data-test="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })

    await input.trigger('change')

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="preview-summary"]').exists()).toBe(true),
    )
    expect(calls.some((call) => call.path === '/api/answers/preview')).toBe(true)
  })

  it('确认导入沿用同一段原文，默认跳过重复并刷新答卷', async () => {
    const calls = stubFetch(handlerFor())
    const { wrapper } = await mountGrading()

    const csv = '学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n'
    await wrapper.find('[data-test="import-text"]').setValue(csv)
    await wrapper.find('[data-test="preview"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-test="confirm-import"]').trigger('click')
    await flushPromises()

    const importCall = calls.find((call) => call.path === '/api/answers/import')
    expect(importCall).toBeDefined()
    expect(JSON.parse(importCall?.init.body as string)).toEqual({
      source: 'csv',
      text: csv,
      skip_duplicates: true,
    })
    expect(successes.join(' ')).toContain('已导入 2 份')
    expect(wrapper.find('[data-test="answer-table"]').text()).toContain('S-0001')
  })

  it('预览不通过时显示原因且不能导入', async () => {
    stubFetch(
      handlerFor({
        preview: {
          ok: false,
          error: '需要 2 列（题号,答案）或 3 列（学号,题号,答案），实际 1 列',
          total_rows: 0,
          rows: [],
          problems: [],
          questions: [],
        },
      }),
    )
    const { wrapper } = await mountGrading()

    await wrapper.find('[data-test="import-text"]').setValue('只有一个字段')
    await wrapper.find('[data-test="preview"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="preview-error"]').text()).toContain('实际 1 列')
    expect(wrapper.find('[data-test="confirm-import"]').attributes('disabled')).toBeDefined()
  })

  it('题号不存在时单独列出缺失的题号，而不是只给一句错误', async () => {
    stubFetch(
      handlerFor({
        preview: {
          ok: false,
          error: '题号不存在：Q-99（先保存这份评分标准，再导入答卷）',
          total_rows: 0,
          rows: [],
          problems: [],
          questions: [{ code: 'Q-99', exists: false }],
          question_codes: ['Q-99'],
        },
      }),
    )
    const { wrapper } = await mountGrading()

    await wrapper.find('[data-test="import-text"]').setValue('题号,答案\nQ-99,随便答点\n')
    await wrapper.find('[data-test="preview"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="preview-error"]').text()).toContain('Q-99')
    expect(wrapper.find('[data-test="missing-question"]').text()).toContain('Q-99')
  })

  it('CSV 里的题号与当前选择不一致时，切题并刷新答卷列表', async () => {
    const rubrics = {
      total: 2,
      items: [
        { ...RUBRICS.items[0], rubric_id: 3, question_code: 'Q-02' },
        RUBRICS.items[0],
      ],
    }
    const calls = stubFetch(handlerFor({ rubrics }))
    const { wrapper } = await mountGrading()

    // 初始选中列表里的第一道题
    expect(calls.some((call) => call.path === '/api/answers?question_code=Q-02')).toBe(true)

    await wrapper.find('[data-test="import-text"]').setValue('题号,答案\nQ-01,客户端发送 SYN\n')
    await wrapper.find('[data-test="preview"]').trigger('click')
    await flushPromises()

    const paths = calls
      .filter((call) => call.path.startsWith('/api/answers?'))
      .map((call) => call.path)
    expect(paths[paths.length - 1]).toBe('/api/answers?question_code=Q-01')
  })

  it('单份粘贴必须带题号，并由文件之外的字段传入', async () => {
    const calls = stubFetch(handlerFor())
    const { wrapper } = await mountGrading()

    await wrapper.find('[data-test="tab-paste"]').trigger('click')
    await wrapper.find('[data-test="import-text"]').setValue('客户端发送 SYN，服务器回复 SYN+ACK')
    await wrapper.find('[data-test="preview"]').trigger('click')
    await flushPromises()

    const previewCall = calls.find((call) => call.path === '/api/answers/preview')
    expect(JSON.parse(previewCall?.init.body as string)).toEqual({
      source: 'paste',
      text: '客户端发送 SYN，服务器回复 SYN+ACK',
      question_code: 'Q-01',
    })
  })

  it('没有评分标准时提示先保存标准', async () => {
    stubFetch(handlerFor({ rubrics: { total: 0, items: [] } }))
    const { wrapper } = await mountGrading()

    expect(wrapper.find('[data-test="no-rubrics"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="no-rubrics"]').text()).toContain('评分标准')
  })
})

describe('发起评分与进度', () => {
  it('勾选答卷后发起评分，并把任务号写进地址栏', async () => {
    const calls = stubFetch(handlerFor())
    const { wrapper, router } = await mountGrading()

    const picks = wrapper.findAll('[data-test="pick-answer"] input')
    await picks[0].setValue(true)
    await picks[1].setValue(true)
    await wrapper.find('[data-test="start-task"]').trigger('click')
    await flushPromises()

    const create = calls.find((call) => call.path === '/api/tasks')
    expect(JSON.parse(create?.init.body as string)).toEqual({
      question_code: 'Q-01',
      answer_ids: [1, 2],
      samples: 3,
    })
    await vi.waitFor(() => expect(router.currentRoute.value.query.task).toBe('9'))
    expect(wrapper.find('[data-test="task-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="task-items"]').text()).toContain('S-0001')
  })

  it('运行中自动刷新，完成后失败项可见并可一键重试', async () => {
    let settled = false
    const calls = stubFetch(
      handlerFor({
        task: () => (settled ? DONE_WITH_FAILURE : taskPayload()),
      }),
    )
    const { wrapper } = await mountGrading('/grading?task=9')

    expect(wrapper.find('[data-test="task-items"]').text()).toContain('评分中')

    settled = true
    await vi.waitFor(
      () => expect(wrapper.find('[data-test="task-items"]').text()).toContain('失败'),
      { timeout: 3000 },
    )
    expect(wrapper.text()).toContain('内部错误：模拟模型故障')
    expect(wrapper.find('[data-test="retry-failed"]').exists()).toBe(true)

    await wrapper.find('[data-test="retry-failed"]').trigger('click')
    await flushPromises()

    const retry = calls.find((call) => call.path === '/api/tasks/9/retry')
    expect(retry).toBeDefined()
    expect(JSON.parse(retry?.init.body as string)).toEqual({ answer_ids: [] })
  })

  it('任务完成后可跳到复核队列，并带上题号', async () => {
    stubFetch(
      handlerFor({
        task: () => ({
          ...DONE_WITH_FAILURE,
          failed: 0,
          items: [DONE_WITH_FAILURE.items[0]],
        }),
      }),
    )
    const { wrapper, router } = await mountGrading('/grading?task=9')

    await wrapper.find('[data-test="to-review"]').trigger('click')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('review'))
    expect(router.currentRoute.value.query.question_code).toBe('Q-01')
  })

  it('中断且失败数为零时仍能继续处理未完成项', async () => {
    const base = taskPayload()
    const calls = stubFetch(
      handlerFor({
        task: () => ({
          ...base,
          status: 'interrupted',
          done: 0,
          failed: 0,
          items: base.items.map((item) => ({
            ...item,
            status: 'pending',
            state: 'pending',
            aggregate_id: null,
            score: null,
            max_score: null,
            route: null,
            confidence: null,
          })),
        }),
      }),
    )
    const { wrapper } = await mountGrading('/grading?task=9')

    const retry = wrapper.find('[data-test="retry-failed"]')
    expect(retry.exists()).toBe(true)
    expect(retry.text()).toContain('继续处理')

    await retry.trigger('click')
    await flushPromises()

    expect(calls.some((call) => call.path === '/api/tasks/9/retry')).toBe(true)
  })

  it('轮询失败时停止并给出继续刷新的入口', async () => {
    let reads = 0
    const fallback = handlerFor()
    stubFetch((call) => {
      if (call.path === '/api/tasks/9') {
        reads += 1
        if (reads === 1) {
          return jsonResponse(taskPayload())
        }
        if (reads === 2) {
          return jsonResponse({ detail: '服务暂时不可用' }, 503)
        }
        return jsonResponse({
          ...DONE_WITH_FAILURE,
          failed: 0,
          items: [DONE_WITH_FAILURE.items[0]],
        })
      }
      return fallback(call)
    })
    const { wrapper } = await mountGrading('/grading?task=9')

    await vi.waitFor(() => expect(wrapper.find('[data-test="poll-error"]').exists()).toBe(true), {
      timeout: 3000,
    })
    expect(wrapper.find('[data-test="poll-error"]').text()).toContain('服务暂时不可用')

    await wrapper.find('[data-test="resume-polling"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="task-status"]').text()).toContain('已完成')
    expect(wrapper.find('[data-test="poll-error"]').exists()).toBe(false)
  })

  it('重试响应仍显示中断时，也会强制再读一次进度', async () => {
    let reads = 0
    let retried = false
    const fallback = handlerFor()
    stubFetch((call) => {
      if (call.path === '/api/tasks/9') {
        reads += 1
        return jsonResponse(DONE_WITH_FAILURE)
      }
      if (call.path === '/api/tasks/9/retry') {
        retried = true
        return jsonResponse({ reset: 1, ...DONE_WITH_FAILURE, status: 'interrupted' })
      }
      return fallback(call)
    })
    const { wrapper } = await mountGrading('/grading?task=9')

    await wrapper.find('[data-test="retry-failed"]').trigger('click')
    await flushPromises()

    await vi.waitFor(() => expect(reads).toBeGreaterThan(1), { timeout: 3000 })
  })

  it('刷新后从地址栏恢复任务，不再重复建任务', async () => {
    const calls = stubFetch(handlerFor())
    const { wrapper } = await mountGrading('/grading?task=9')

    expect(calls.some((call) => call.path === '/api/tasks/9')).toBe(true)
    expect(calls.some((call) => call.path === '/api/tasks')).toBe(false)
    expect(wrapper.find('[data-test="task-card"]').exists()).toBe(true)
  })
})
