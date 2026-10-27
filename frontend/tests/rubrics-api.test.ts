import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  issueKey,
  listRubrics,
  saveRubric,
  validateRubric,
} from '../src/api/rubrics'
import { RUBRIC, SAVED } from './rubric-fixture'
import { stubFetch, jsonResponse } from './helpers'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('rubrics api', () => {
  it('校验时把整份标准发给服务端', async () => {
    const calls = stubFetch(() => jsonResponse({ usable: true, issues: [] }))

    await validateRubric(RUBRIC)

    const body = JSON.parse(calls[0].init.body as string)
    expect(body.rubric.question_id).toBe('Q-01')
    expect(calls[0].path).toBe('/api/rubrics/validate')
  })

  it('首次保存用 POST，编辑已有版本用 PUT', async () => {
    const calls = stubFetch(() => jsonResponse(SAVED))

    await saveRubric({ rubric: RUBRIC, source_text: '原话' })
    await saveRubric({ rubric: RUBRIC, source_text: '原话' }, 7)

    expect(calls[0].path).toBe('/api/rubrics')
    expect(calls[0].init.method).toBe('POST')
    expect(calls[1].path).toBe('/api/rubrics/7')
    expect(calls[1].init.method).toBe('PUT')
  })

  it('列表请求返回 items', async () => {
    stubFetch(() => jsonResponse({ total: 1, items: [{ rubric_id: 7 }] }))

    await expect(listRubrics()).resolves.toMatchObject({ total: 1 })
  })

  it('提示的 key 由 code 与 message 共同决定', () => {
    expect(issueKey({ code: 'A', message: 'm', blocking: false })).toBe('A|m')
    expect(issueKey({ code: 'A', message: 'n', blocking: false })).not.toBe('A|m')
  })
})
