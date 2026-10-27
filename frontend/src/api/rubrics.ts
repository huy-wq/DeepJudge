import { request } from './client'

export interface RubricIssue {
  code: string
  message: string
  blocking: boolean
}

export interface RubricLevel {
  level: number
  score: number
  criterion: string
}

export interface RubricItem {
  id: string
  desc: string
  weight: number
  levels: RubricLevel[]
  must_hit?: boolean
  acceptable?: string[]
}

export interface RubricDeduction {
  id: string
  desc: string
  penalty: number
  cap: number
}

export interface RubricData {
  question_id: string
  stem: string
  reference_answer: string
  total_score: number
  items: RubricItem[]
  deductions: RubricDeduction[]
  rules: Record<string, unknown>
}

export interface RubricListItem {
  rubric_id: number
  question_code: string
  version: number
  stem: string
  total_score: number
  item_count: number
  created_at: string
}

export interface RubricRecord {
  rubric_id: number
  version: number
  rubric: RubricData
  checksum: string
  source_text: string
  feedback: string
  created_at: string
  parent_rubric_id: number | null
  question_code: string
  issues: RubricIssue[]
}

export interface RubricChange {
  id: string
  type: 'item' | 'deduction'
  change: 'added' | 'removed' | 'updated'
  fields?: string[]
}

export interface GeneratedRubric {
  rubric: RubricData
  summary: string
  preview: unknown[]
  checklist: RubricIssue[]
  attempts: number
  changes?: RubricChange[]
  based_on?: { rubric_id: number; version: number; question_code: string }
}

export interface SavedRubric {
  rubric_id: number
  question_code: string
  version: number
  checksum: string
  parent_rubric_id: number | null
  reused: boolean
  created_at: string
  issues: RubricIssue[]
}

export interface ValidateResult {
  usable: boolean
  issues: RubricIssue[]
}

export interface GeneratePayload {
  stem: string
  total_score: number
  standard_text: string
  reference_answer?: string
  question_id?: string
}

export interface SavePayload {
  rubric: RubricData
  source_text: string
  feedback?: string
}

/** 一条提示的稳定标识：内容变了就应当重新确认。 */
export function issueKey(issue: RubricIssue): string {
  return `${issue.code}|${issue.message}`
}

export function generateRubric(payload: GeneratePayload): Promise<GeneratedRubric> {
  return request<GeneratedRubric>('/api/rubrics/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function iterateRubric(rubricId: number, feedback: string): Promise<GeneratedRubric> {
  return request<GeneratedRubric>(`/api/rubrics/${rubricId}/iterate`, {
    method: 'POST',
    body: JSON.stringify({ feedback }),
  })
}

export function validateRubric(rubric: RubricData): Promise<ValidateResult> {
  return request<ValidateResult>('/api/rubrics/validate', {
    method: 'POST',
    body: JSON.stringify({ rubric }),
  })
}

export function listRubrics(): Promise<{ total: number; items: RubricListItem[] }> {
  return request<{ total: number; items: RubricListItem[] }>('/api/rubrics')
}

export function getRubric(rubricId: number): Promise<RubricRecord> {
  return request<RubricRecord>(`/api/rubrics/${rubricId}`)
}

export function saveRubric(
  payload: SavePayload,
  baseId?: number,
): Promise<SavedRubric> {
  const path = baseId === undefined ? '/api/rubrics' : `/api/rubrics/${baseId}`
  return request<SavedRubric>(path, {
    method: baseId === undefined ? 'POST' : 'PUT',
    body: JSON.stringify(payload),
  })
}

/** 没有模型摘要时（例如重新打开已保存版本）用采分点拼一句，与服务端兜底一致。 */
export function composeSummary(rubric: RubricData): string {
  const weights = rubric.items.map((item) => `${item.id} ${item.weight} 分`).join('、')
  const deductions = rubric.deductions.length
    ? `；另有 ${rubric.deductions.length} 个扣分项`
    : ''
  return `围绕题干共设 ${rubric.items.length} 个采分点（${weights}），满分 ${rubric.total_score} 分${deductions}。`
}
