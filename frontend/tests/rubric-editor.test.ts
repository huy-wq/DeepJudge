import ElementPlus from 'element-plus'
import { flushPromises, enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { createMemoryHistory } from 'vue-router'

import { setErrorNotifier, setSuccessNotifier } from '../src/feedback'
import { createAppRouter } from '../src/router'
import { resetSessionForTests } from '../src/session'
import { GENERATED, RECORD, RUBRIC, SAVED } from './rubric-fixture'
import { emptyResponse, jsonResponse, stubFetch, type FetchCall } from './helpers'

enableAutoUnmount(afterEach)

let successes: string[] = []
let errors: string[] = []

function validateReply(call: FetchCall): Response {
  const rubric = JSON.parse(call.init.body as string).rubric
  const sum = rubric.items.reduce((total: number, item: { weight: number }) => total + item.weight, 0)
  const issues: Array<{ code: string; message: string; blocking: boolean }> = []
  if (Math.abs(sum - rubric.total_score) > 1e-9) {
    issues.push({
      code: 'WEIGHT_SUM',
      message: `采分点权重合计 ${sum} 与满分 ${rubric.total_score} 不一致：超出 1 分`,
      blocking: true,
    })
  }
  const vague = rubric.items.some((item: { levels: Array<{ criterion: string }> }) =>
    item.levels.some((level) => level.criterion.includes('论述充分')),
  )
  if (vague) {
    issues.push({
      code: 'VAGUE_CRITERION',
      message: 'P1: 等级 0.5 的判定依据包含不可验证的措辞「论述充分」',
      blocking: false,
    })
  }
  return jsonResponse({ usable: !issues.some((issue) => issue.blocking), issues })
}

function handler(call: FetchCall): Response {
  const method = (call.init.method ?? 'GET').toUpperCase()
  if (call.path === '/api/auth/session') {
    return jsonResponse({ authenticated: true, expires_at: 1 })
  }
  if (call.path === '/api/rubrics/validate' && method === 'POST') {
    return validateReply(call)
  }
  if (call.path === '/api/rubrics/generate' && method === 'POST') {
    return jsonResponse(GENERATED)
  }
  if (call.path === '/api/rubrics/7' && method === 'GET') {
    return jsonResponse(RECORD)
  }
  if (call.path === '/api/rubrics/101' && method === 'GET') {
    return jsonResponse({ ...RECORD, rubric_id: 101, version: 1, parent_rubric_id: null })
  }
  if (call.path === '/api/rubrics/7/iterate' && method === 'POST') {
    return jsonResponse({
      ...GENERATED,
      changes: [{ id: 'P1', type: 'item', change: 'updated', fields: ['desc'] }],
      based_on: { rubric_id: 7, version: 2, question_code: 'Q-01' },
    })
  }
  if (call.path === '/api/rubrics/7' && method === 'PUT') {
    return jsonResponse(SAVED)
  }
  if (call.path === '/api/rubrics' && method === 'POST') {
    return jsonResponse(SAVED)
  }
  if (call.path === '/api/auth/logout') {
    return emptyResponse()
  }
  return jsonResponse({ detail: `unexpected ${method} ${call.path}` }, 500)
}

async function mountEditor(path: string) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  // 用 router-view 挂载：这样 onBeforeRouteLeave 才会真正注册到路由记录上
  const Host = defineComponent({ template: '<router-view />' })
  const wrapper = mount(Host, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return { wrapper, router }
}

async function fillNewForm(wrapper: Awaited<ReturnType<typeof mountEditor>>['wrapper']) {
  await wrapper.find('[data-test="stem"]').setValue('简述 TCP 三次握手')
  await wrapper
    .find('[data-test="standard-text"]')
    .setValue('三个要点：客户端发 SYN 给 3 分；服务器回 SYN+ACK 给 4 分；客户端回 ACK 给 3 分。')
}

async function generateDraft(wrapper: Awaited<ReturnType<typeof mountEditor>>['wrapper']) {
  await fillNewForm(wrapper)
  await wrapper.find('[data-test="generate"]').trigger('click')
  await flushPromises()
  await vi.waitFor(() => expect(wrapper.find('[data-test="preview"]').exists()).toBe(true))
}

async function waitForSaveEnabled(wrapper: Awaited<ReturnType<typeof mountEditor>>['wrapper']) {
  await vi.waitFor(
    () => {
      expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeUndefined()
    },
    { timeout: 3000 },
  )
}

beforeEach(() => {
  resetSessionForTests()
  successes = []
  errors = []
  setSuccessNotifier((message) => successes.push(message))
  setErrorNotifier((message) => errors.push(message))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setSuccessNotifier(null)
  setErrorNotifier(null)
})

describe('标准编辑页', () => {
  it('打开已保存的标准时，摘要 / 预览表 / 校验清单同屏可见', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/7')

    // 三块在同一个内容区里并列呈现，不是互斥的标签页
    const right = wrapper.find('.right-pane')
    expect(right.find('[data-test="summary"]').exists()).toBe(true)
    expect(right.find('[data-test="preview-block"]').exists()).toBe(true)
    expect(right.find('[data-test="checklist-block"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="summary"]').text()).toContain('采分点')
    expect(wrapper.find('[data-test="preview"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="checklist"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="checklist-ok"]').exists()).toBe(true)
    expect(
      (wrapper.find('[data-test="stem"]').element as HTMLTextAreaElement).value,
    ).toContain('三次握手')
    // 已保存版本：题号锁定，避免误改到另一道题
    expect(wrapper.find('[data-test="question-code"]').attributes('disabled')).toBeDefined()
  })

  it('生成草稿后三块内容一起出现', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/new')

    await generateDraft(wrapper)
    expect(wrapper.find('[data-test="summary"]').text()).toContain('三个采分点')
    expect(wrapper.findAll('[data-test="item-desc"]')).toHaveLength(3)
    expect(wrapper.find('[data-test="checklist-ok"]').exists()).toBe(true)
  })

  it('改动某一行后校验清单实时更新，结构错误禁用保存并标红', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)
    await waitForSaveEnabled(wrapper)

    const weight = wrapper.findAll('[data-test="item-weight"] input')[0]
    await weight.setValue('4')
    await weight.trigger('change')

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="blocking-issue"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="blocking-issue"]').text()).toContain('超出')
    expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="save-hint"]').text()).toContain('结构错误')
  })

  it('语义问题标黄，勾选已知悉后才能保存', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)
    await waitForSaveEnabled(wrapper)

    const criterion = wrapper.findAll('[data-test="level-criterion"]')[1]
    await criterion.setValue('论述充分即可')
    await criterion.trigger('change')

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="warning-issue"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="warning-issue"]').text()).toContain('论述充分')
    expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()

    await wrapper.find('[data-test="acknowledge"] input').setValue(true)

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeUndefined()
    })
  })

  it('保存成功后给出反馈，并跳到可重新打开的版本地址', async () => {
    const calls = stubFetch(handler)
    const { wrapper, router } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)
    await waitForSaveEnabled(wrapper)

    await wrapper.find('[data-test="save"]').trigger('click')

    await vi.waitFor(() => {
      const saveCall = calls.find(
        (call) => call.path === '/api/rubrics' && call.init.method === 'POST',
      )
      expect(saveCall).toBeDefined()
      const body = JSON.parse(saveCall?.init.body as string)
      expect(body.source_text).toContain('三个要点')
      expect(body.rubric.items).toHaveLength(3)
    })
    await vi.waitFor(() => {
      expect(router.currentRoute.value.fullPath).toBe('/rubrics/101')
    })
    expect(successes.join('')).toContain('已保存为第 1 版')
  })

  it('迭代时把意见发给上一版，并展示服务端算出的变更清单', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/7')

    await wrapper.find('[data-test="feedback"]').setValue('P1 的描述写具体一点')
    await wrapper.find('[data-test="iterate"]').trigger('click')
    await flushPromises()

    const iterateCall = calls.find((call) => call.path === '/api/rubrics/7/iterate')
    expect(JSON.parse(iterateCall?.init.body as string).feedback).toContain('写具体')
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="changes"]').text()).toContain('P1')
    })
  })

  it('没有评分标准原文时不能保存', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)

    await wrapper.find('[data-test="standard-text"]').setValue('')

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()
    })
    expect(wrapper.find('[data-test="save-hint"]').text()).toContain('评分标准原文')
  })

  it('编辑已保存版本时用 PUT，并带上输入的意见', async () => {
    const calls = stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/7')

    await wrapper.find('[data-test="feedback"]').setValue('P1 改具体')
    await wrapper.find('[data-test="iterate"]').trigger('click')
    await flushPromises()
    await waitForSaveEnabled(wrapper)
    await wrapper.find('[data-test="save"]').trigger('click')

    await vi.waitFor(() => {
      const put = calls.find(
        (call) => call.path === '/api/rubrics/7' && call.init.method === 'PUT',
      )
      expect(put).toBeDefined()
      expect(JSON.parse(put?.init.body as string).feedback).toBe('P1 改具体')
    })
  })

  it('没有生成或打开标准时，保存按钮不可用', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/new')

    expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="save-hint"]').text()).toContain('生成草稿')
  })

  it('保存前会再校验一次；网络失败时不写入', async () => {
    const calls = stubFetch((call) => {
      if (call.path === '/api/rubrics/validate') {
        return jsonResponse({ detail: '服务暂时不可用' }, 503)
      }
      return handler(call)
    })
    const { wrapper } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)
    await waitForSaveEnabled(wrapper)

    await wrapper.find('[data-test="save"]').trigger('click')
    await flushPromises()

    expect(errors.join('')).toContain('服务暂时不可用')
    // 校验失败时不能继续显示"没有问题"，也不能放开保存
    expect(wrapper.find('[data-test="validation-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="save-hint"]').text()).toContain('校验失败')
    expect(calls.some((call) => call.path === '/api/rubrics' && call.init.method === 'POST')).toBe(
      false,
    )
  })

  it('改动权重后摘要跟着更新，不再显示旧数字', async () => {
    stubFetch(handler)
    const { wrapper } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)
    expect(wrapper.find('[data-test="summary-text"]').text()).toContain('三个采分点')

    const weight = wrapper.findAll('[data-test="item-weight"] input')[0]
    await weight.setValue('4')
    await weight.trigger('change')

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="summary-text"]').text()).toContain('P1 4 分')
    })
  })

  it('重新打开带提示的版本时仍需重新确认', async () => {
    const withWarning = {
      ...RECORD,
      issues: [
        { code: 'VAGUE_CRITERION', message: 'P1: 判定依据含糊', blocking: false },
      ],
    }
    stubFetch((call) =>
      call.path === '/api/rubrics/7' ? jsonResponse(withWarning) : handler(call),
    )
    const { wrapper } = await mountEditor('/rubrics/7')

    expect(wrapper.find('[data-test="warning-issue"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()

    await wrapper.find('[data-test="acknowledge"] input').setValue(true)

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeUndefined()
    })
  })

  it('迭代进行中时保存与再次迭代都不可用', async () => {
    let release: (response: Response) => void = () => {}
    const pending = new Promise<Response>((resolve) => {
      release = resolve
    })
    stubFetch((call) => (call.path === '/api/rubrics/7/iterate' ? pending : handler(call)))
    const { wrapper } = await mountEditor('/rubrics/7')

    await wrapper.find('[data-test="feedback"]').setValue('P1 改具体')
    await wrapper.find('[data-test="iterate"]').trigger('click')
    await nextTick()

    expect(wrapper.find('[data-test="save"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="iterate"]').attributes('disabled')).toBeDefined()

    release(jsonResponse({ ...GENERATED, changes: [] }))
    await flushPromises()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="iterate"]').attributes('disabled')).toBeUndefined()
    })
  })

  it('有未保存改动时离开页面会先确认', async () => {
    stubFetch(handler)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { wrapper, router } = await mountEditor('/rubrics/new')
    await generateDraft(wrapper)

    await router.push('/rubrics').catch(() => {})

    expect(confirm).toHaveBeenCalled()
    expect(router.currentRoute.value.fullPath).toBe('/rubrics/new')
  })
})
