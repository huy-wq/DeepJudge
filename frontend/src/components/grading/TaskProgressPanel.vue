<script setup lang="ts">
import { computed } from 'vue'

import type { TaskDetail, TaskItem, TaskItemState, TaskStatus } from '../../api/tasks'

const props = defineProps<{
  task: TaskDetail
  /** 轮询失败时的可读原因；空串表示自动刷新正常 */
  pollError: string
}>()

const emit = defineEmits<{
  retry: []
  retryOne: [answerId: number]
  review: []
  resume: []
}>()

const progressPercent = computed(() => {
  if (props.task.total <= 0) {
    return 0
  }
  return Math.min(
    100,
    Math.round(((props.task.done + props.task.failed) / props.task.total) * 100),
  )
})

const progressStatus = computed(() => {
  if (props.task.status === 'interrupted') {
    return 'exception' as const
  }
  if (props.task.status === 'done') {
    return 'success' as const
  }
  return undefined
})

const reviewCount = computed(
  () => props.task.items.filter((item) => item.state === 'needs_review').length,
)

const taskStatusLabel: Record<TaskStatus, string> = {
  pending: '排队中',
  running: '评分中',
  done: '已完成',
  interrupted: '已中断',
}

const taskStatusType: Record<TaskStatus, 'info' | 'warning' | 'success' | 'danger'> = {
  pending: 'info',
  running: 'warning',
  done: 'success',
  interrupted: 'danger',
}

const itemStateLabel: Record<TaskItemState, string> = {
  graded: '已评',
  needs_review: '待复核',
  finalized: '已定稿',
  superseded: '已被重评',
  failed: '失败',
  pending: '等待',
  running: '评分中',
}

const itemStateType: Record<TaskItemState, 'success' | 'warning' | 'info' | 'danger'> = {
  graded: 'success',
  needs_review: 'warning',
  finalized: 'success',
  superseded: 'info',
  failed: 'danger',
  pending: 'info',
  running: 'info',
}

function routeLabel(route: TaskItem['route']): string {
  if (route === 'human') {
    return '人工复核'
  }
  return route === 'auto' ? '自动通过' : '—'
}

// 表格插槽里的 row 是 any，直接用它索引映射会丢类型；这两个函数把类型收窄回来
function stateLabel(state: TaskItemState): string {
  return itemStateLabel[state]
}

function stateType(state: TaskItemState): 'success' | 'warning' | 'info' | 'danger' {
  return itemStateType[state]
}
</script>

<template>
  <el-card class="task-card" data-test="task-card">
    <template #header>
      <div class="card-head">
        <span>3 任务进度</span>
        <el-tag :type="taskStatusType[task.status]" size="small" data-test="task-status">
          {{ taskStatusLabel[task.status] }}
        </el-tag>
      </div>
    </template>

    <div class="task-meta">
      <span>题号 {{ task.question_code }}</span>
      <span>已评 {{ task.done }} / 失败 {{ task.failed }} / 共 {{ task.total }}</span>
      <span>采样 {{ task.samples }} 次</span>
      <span>标准版本 #{{ task.rubric_id }}</span>
    </div>

    <el-progress :percentage="progressPercent" :status="progressStatus" />

    <el-alert
      v-if="task.error"
      type="error"
      :closable="false"
      class="task-error"
      :title="task.error"
      data-test="task-error"
    />
    <el-alert
      v-if="task.status === 'interrupted'"
      type="warning"
      :closable="false"
      class="task-error"
      title="任务被中断（进程重启或执行器异常）。失败项可以重试；没有变成失败的份数会留在“等待”。"
      data-test="task-interrupted"
    />
    <el-alert
      v-if="pollError"
      type="warning"
      :closable="false"
      class="task-error"
      data-test="poll-error"
    >
      <template #title>自动刷新已停止：{{ pollError }}</template>
      <el-button link type="primary" data-test="resume-polling" @click="emit('resume')">
        继续刷新
      </el-button>
    </el-alert>

    <el-table :data="task.items" size="small" data-test="task-items" max-height="360">
      <el-table-column prop="pseudo_id" label="答卷" width="108" />
      <el-table-column label="状态" width="104">
        <template #default="{ row }">
          <el-tag :type="stateType(row.state)" size="small">
            {{ stateLabel(row.state) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模型分" width="106">
        <template #default="{ row }">
          <span v-if="row.score !== null">{{ row.score }} / {{ row.max_score }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="定稿分" width="88">
        <template #default="{ row }">
          <span v-if="row.final_score !== null">{{ row.final_score }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="路由" width="104">
        <template #default="{ row }">{{ routeLabel(row.route) }}</template>
      </el-table-column>
      <el-table-column label="说明" min-width="220">
        <template #default="{ row }">
          <span v-if="row.error" class="error-text">{{ row.error }}</span>
          <span v-else-if="!row.current" class="muted">这份结论已被后来的重评取代</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="88">
        <template #default="{ row }">
          <el-button
            v-if="row.state === 'failed'"
            link
            type="primary"
            :disabled="task.status === 'running'"
            data-test="retry-one"
            @click="emit('retryOne', row.answer_id)"
          >
            重试
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="actions">
      <el-button
        v-if="task.failed || task.status === 'interrupted'"
        :loading="task.status === 'running'"
        :disabled="task.status === 'running'"
        data-test="retry-failed"
        @click="emit('retry')"
      >
        {{ task.failed ? `重试全部失败项（${task.failed}）` : '继续处理未完成项' }}
      </el-button>
      <el-button
        v-if="reviewCount && task.status !== 'running' && task.status !== 'pending'"
        type="primary"
        plain
        data-test="to-review"
        @click="emit('review')"
      >
        去复核队列（{{ reviewCount }} 份）
      </el-button>
      <span v-else-if="task.status === 'done'" class="muted" data-test="no-review">
        本批没有待复核的结论；结果见上表
      </span>
    </div>
  </el-card>
</template>

<style scoped>
.task-card {
  margin-bottom: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.task-meta {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  color: var(--el-text-color-regular);
  font-size: 14px;
}

.task-error {
  margin-bottom: 12px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.error-text {
  color: var(--el-color-danger);
}
</style>
