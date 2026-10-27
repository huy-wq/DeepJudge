import { request } from './client'

export type ImportSource = 'csv' | 'paste'

export interface PreviewRow {
  line: number
  question_code: string
  answer_text: string
  duplicate: boolean
}

export interface PreviewProblem {
  line: number
  reason: string
}

export interface PreviewQuestion {
  code: string
  exists: boolean
  total_score?: number
}

export interface PreviewResult {
  ok: boolean
  error: string | null
  total_rows: number
  blank_lines?: number
  /** 成功解析且带学号的行数；学号本身从不出现在响应里 */
  rows_with_student_id?: number
  question_codes?: string[]
  questions: PreviewQuestion[]
  rows: PreviewRow[]
  problems: PreviewProblem[]
}

export interface ImportPayload {
  source: ImportSource
  text: string
  /** 单份粘贴必填；CSV 留空时题号从文件里读 */
  question_code?: string
  skip_duplicates?: boolean
}

export interface ImportResult {
  imported: number
  skipped_duplicates: number
  problems?: PreviewProblem[]
  error: string | null
}

export interface AnswerListItem {
  answer_id: number
  pseudo_id: string
  source: string
  created_at: string
  length: number
}

export interface AnswerList {
  question_code: string
  total: number
  items: AnswerListItem[]
}

export function previewAnswers(payload: ImportPayload): Promise<PreviewResult> {
  return request<PreviewResult>('/api/answers/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function importAnswers(payload: ImportPayload): Promise<ImportResult> {
  return request<ImportResult>('/api/answers/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listAnswers(questionCode: string): Promise<AnswerList> {
  return request<AnswerList>(`/api/answers?question_code=${encodeURIComponent(questionCode)}`)
}
