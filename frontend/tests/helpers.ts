import { vi } from 'vitest'

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

export function emptyResponse(status = 204): Response {
  return new Response(null, { status })
}

export interface FetchCall {
  path: string
  init: RequestInit
}

/** 用一份"路径 → 响应"的映射替代全局 fetch，并记录每次调用的路径与参数。 */
export function stubFetch(
  handler: (call: FetchCall) => Response | Promise<Response>,
): FetchCall[] {
  const calls: FetchCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const call = { path: String(input), init }
      calls.push(call)
      return handler(call)
    }),
  )
  return calls
}
