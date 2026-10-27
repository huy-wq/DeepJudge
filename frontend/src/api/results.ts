import { request } from './client'

export interface ResultRow {
  aggregate_id: number
  answer_id: number
  pseudo_id: string
  question_code: string
  raw_score: number
  final_score: number | null
  max_score: number
  route: 'auto' | 'human'
  risk: number
  confidence: number
  dispersion: number
  total_range: number
  grounding_rate: number
  /** 转人工的原因，服务端按同一套路由规则生成 */
  reasons: string[]
  created_at: string
  reviewed: boolean
}

export interface ResultList {
  route: 'auto' | 'human' | null
  pending: boolean
  total: number
  limit: number
  offset: number
  items: ResultRow[]
}

export interface ResultQuery {
  route?: 'auto' | 'human'
  pending?: boolean
  question_code?: string
  limit?: number
  offset?: number
}

export function listResults(query: ResultQuery = {}): Promise<ResultList> {
  const params = new URLSearchParams()
  if (query.route !== undefined) {
    params.set('route', query.route)
  }
  if (query.pending !== undefined) {
    params.set('pending', String(query.pending))
  }
  if (query.question_code) {
    params.set('question_code', query.question_code)
  }
  if (query.limit !== undefined) {
    params.set('limit', String(query.limit))
  }
  if (query.offset !== undefined) {
    params.set('offset', String(query.offset))
  }
  const suffix = params.toString()
  return request<ResultList>(`/api/results${suffix ? `?${suffix}` : ''}`)
}
