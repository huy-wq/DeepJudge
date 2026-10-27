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

export interface ResultSpan {
  start: number
  end: number
}

export interface ResultLevel {
  level: number
  score: number
  criterion: string
}

export interface ResultSample {
  run_id: number
  hit: boolean
  grounded: boolean
  evidence: string
  span: ResultSpan | null
  level?: number | null
}

export interface ResultItem {
  id: string
  desc: string
  weight: number
  levels: ResultLevel[]
  /** 多次采样聚合后的等级与得分（服务端算好的） */
  level: number
  score: number
  sample_levels: Array<number | null>
  disagree: boolean
  hit: boolean
  grounded: boolean
  evidence: string
  span: ResultSpan | null
  reason: string
  samples: ResultSample[]
}

export interface ResultDeduction {
  id: string
  desc: string
  penalty: number
  cap: number
  applied: boolean
  hit_samples: number
  evidence: string
  span: ResultSpan | null
  reason: string
  samples: ResultSample[]
}

export interface ResultReview {
  review_id: number
  aggregate_id: number
  reviewer_id: string
  final_score: number
  decision: 'accept' | 'override'
  comment: string
  created_at: string | null
}

export interface ResultDetail {
  aggregate_id: number
  /** 是不是该答卷当前最新的结论；旧链接打开旧结论时为 false */
  current: boolean
  superseded_by: number | null
  answer: {
    answer_id: number
    pseudo_id: string
    text: string
    truncated: boolean
  }
  rubric: {
    question_id: string
    stem: string
    reference_answer: string
    total_score: number
  }
  score: {
    raw: number
    final: number | null
    max: number
    route: 'auto' | 'human'
    risk: number
    reasons: string[]
    dispersion: number
    total_range: number
    confidence: number
    grounding_rate: number
    sample_totals: number[]
    applied_deductions: string[]
  }
  items: ResultItem[]
  deductions: ResultDeduction[]
  samples: Array<{ run_id: number; score: number; levels: Record<string, number> }>
  review: ResultReview | null
}

export interface ReviewPayload {
  decision: 'accept' | 'override'
  /** 仅 override 需要；服务端会校验有限性与 [0, max] */
  final_score?: number
  /** override 必填；accept 可空 */
  comment?: string
}

export interface BatchAcceptResult {
  reviews: ResultReview[]
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

export function getResult(aggregateId: number): Promise<ResultDetail> {
  return request<ResultDetail>(`/api/results/${aggregateId}`)
}

export function reviewResult(
  aggregateId: number,
  payload: ReviewPayload,
): Promise<ResultReview> {
  return request<ResultReview>(`/api/results/${aggregateId}/review`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** 批量通过只接受 route='auto' 的结果；混入待人工复核的会整批拒绝。 */
export function batchAccept(
  aggregateIds: number[],
  comment = '',
): Promise<BatchAcceptResult> {
  return request<BatchAcceptResult>('/api/results/batch-accept', {
    method: 'POST',
    body: JSON.stringify({ aggregate_ids: aggregateIds, comment }),
  })
}
