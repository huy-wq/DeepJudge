import { beforeEach } from 'vitest'

import { setErrorNotifier } from '../src/feedback'

// 默认把全局提示换成空实现：测试只在自己关心时替换 notifier，
// 否则 ElMessage 会在 document.body 里留下 toast 与定时器，污染其他用例。
beforeEach(() => {
  setErrorNotifier(() => {})
})

// jsdom 不实现这几个浏览器 API，Element Plus 的部分组件会直接用到它们
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver = globalThis.ResizeObserver ?? (ResizeObserverStub as never)
window.scrollTo = window.scrollTo ?? (() => {})
