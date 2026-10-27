import { ElMessage } from 'element-plus'

import { errorMessage } from './api/client'

/**
 * 统一的可读错误提示。
 *
 * 所有页面都通过 `notifyError` 报错，而不是各自 `ElMessage.error(...)`：
 * 提示文案只在这里兜底一次，测试也能替换掉 notifier（见 setErrorNotifier）。
 */
export type ErrorNotifier = (message: string) => void

const defaultNotifier: ErrorNotifier = (message) => {
  ElMessage.error(message)
}

let notifier: ErrorNotifier = defaultNotifier

export function setErrorNotifier(next: ErrorNotifier | null): void {
  notifier = next ?? defaultNotifier
}

export { errorMessage }

export function notifyError(error: unknown): string {
  const message = errorMessage(error)
  notifier(message)
  return message
}
