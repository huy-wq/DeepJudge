import { request } from './client'

export type TaskStatus = 'pending' | 'running' | 'done' | 'interrupted'

export type TaskItemState =
  | 'graded'
  | 'needs_review'
  | 'finalized'
  | 'superseded'
  | 'failed'
  | 'pending'
  | 'running'

export interface TaskItem {
  answer_id: number
  pseudo_id: string
  status: string
  error: string | null
  aggregate_id: number | null
  /** 这个结果是否仍是该答卷的最新结论（被重评取代后为 false） */
  current: boolean
  score: number | null
  max_score: number | null
  route: 'auto' | 'human' | null
  confidence: number | null
  final_score: number | null
  state: TaskItemState
}

export interface TaskDetail {
  task_id: number
  question_code: string
  rubric_id: number
  samples: number
  status: TaskStatus
  total: number
  done: number
  failed: number
  error: string | null
  items: TaskItem[]
}

export interface CreateTaskInput {
  question_code: string
  answer_ids: number[]
  samples: number
}

export interface RetryResult extends TaskDetail {
  /** 这次被放回 pending 的失败项数；0 不代表没活干（中断任务可能只剩下 pending 项） */
  reset: number
}

export function createTask(payload: CreateTaskInput): Promise<TaskDetail> {
  return request<TaskDetail>('/api/tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getTask(taskId: number): Promise<TaskDetail> {
  return request<TaskDetail>(`/api/tasks/${taskId}`)
}

/** 传空数组表示重试本任务的全部失败项。 */
export function retryTask(taskId: number, answerIds: number[] = []): Promise<RetryResult> {
  return request<RetryResult>(`/api/tasks/${taskId}/retry`, {
    method: 'POST',
    body: JSON.stringify({ answer_ids: answerIds }),
  })
}
