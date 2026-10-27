import ElementPlus from 'element-plus'
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ErrorBoundary from '../src/components/ErrorBoundary.vue'
import { setErrorNotifier } from '../src/feedback'

afterEach(() => {
  vi.restoreAllMocks()
  setErrorNotifier(null)
})

describe('ErrorBoundary', () => {
  it('子组件渲染失败时显示可读错误卡片，而不是白屏', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const captured: string[] = []
    setErrorNotifier((message) => captured.push(message))
    const Boom = defineComponent({
      render() {
        throw new Error('组件炸了')
      },
    })

    const wrapper = mount(ErrorBoundary, {
      slots: { default: () => h(Boom) },
      global: { plugins: [ElementPlus] },
    })

    await nextTick()
    expect(wrapper.text()).toContain('页面出错了')
    expect(wrapper.text()).toContain('组件炸了')
    expect(captured).toEqual(['组件炸了'])
  })
})
