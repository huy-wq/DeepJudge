<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  importAnswers,
  listAnswers,
  previewAnswers,
  type AnswerListItem,
  type ImportPayload,
  type ImportSource,
  type PreviewProblem,
  type PreviewResult,
} from '../api/answers'
import { listRubrics, type RubricListItem } from '../api/rubrics'
import { createTask, getTask, retryTask, type TaskDetail } from '../api/tasks'
import { notifyError, notifySuccess } from '../feedback'
import PreviewPanel from '../components/grading/PreviewPanel.vue'
import TaskProgressPanel from '../components/grading/TaskProgressPanel.vue'

const route = useRoute()
const router = useRouter()

// ---------------------------------------------------------------- 表单状态
const rubrics = ref<RubricListItem[]>([])
const rubricsLoading = ref(false)
const questionCode = ref('')

const source = ref<ImportSource>('csv')
const importText = ref('')
const skipDuplicates = ref(true)
const preview = ref<PreviewResult | null>(null)
const previewing = ref(false)
const importing = ref(false)
const importedCount = ref(0)
const lastProblems = ref<PreviewProblem[]>([])

const answers = ref<AnswerListItem[]>([])
const answerTotal = ref(0)
const answersLoading = ref(false)
const selectedIds = ref<number[]>([])
const samples = ref(3)
const starting = ref(false)

const task = ref<TaskDetail | null>(null)

const MAX_FILE_BYTES = 2 * 1024 * 1024
const POLL_MS = 1000

// ---------------------------------------------------------------- 计算属性
const questionOptions = computed(() =>
  rubrics.value.map((rubric) => ({
    value: rubric.question_code,
    label: `${rubric.question_code} · ${rubric.stem}（满分 ${rubric.total_score}）`,
  })),
)

const canImport = computed(
  () => Boolean(preview.value?.ok) && (preview.value?.rows.length ?? 0) > 0 && !importing.value,
)

// ---------------------------------------------------------------- 载入
async function loadRubrics(): Promise<void> {
  rubricsLoading.value = true
  try {
    rubrics.value = (await listRubrics()).items
    if (!questionCode.value && rubrics.value.length) {
      questionCode.value = rubrics.value[0].question_code
    }
  } catch (error) {
    notifyError(error)
  } finally {
    rubricsLoading.value = false
  }
}

let answersSeq = 0

async function loadAnswers(): Promise<void> {
  if (!questionCode.value) {
    answers.value = []
    answerTotal.value = 0
    selectedIds.value = []
    return
  }
  const seq = ++answersSeq
  answersLoading.value = true
  try {
    const payload = await listAnswers(questionCode.value)
    if (seq !== answersSeq) {
      return
    }
    answers.value = payload.items
    answerTotal.value = payload.total
    const available = new Set(payload.items.map((item) => item.answer_id))
    selectedIds.value = selectedIds.value.filter((id) => available.has(id))
  } catch (error) {
    if (seq === answersSeq) {
      notifyError(error)
    }
  } finally {
    if (seq === answersSeq) {
      answersLoading.value = false
    }
  }
}

async function loadTask(taskId: number): Promise<boolean> {
  try {
    const payload = await getTask(taskId)
    task.value = payload
    questionCode.value = payload.question_code
    await loadAnswers()
    return true
  } catch (error) {
    notifyError(error)
    return false
  }
}

async function bootstrap(): Promise<void> {
  await loadRubrics()
  const taskId = parseTaskId(route.query.task)
  if (taskId !== null) {
    if (await loadTask(taskId)) {
      schedulePolling()
      return
    }
  }
  await loadAnswers()
}

function parseTaskId(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string' || !/^\d+$/.test(raw)) {
    return null
  }
  return Number(raw)
}

// ---------------------------------------------------------------- 导入
let previewSeq = 0

/** 预览回来时，请求发出去的那段文本/题号是否仍是页面上现在的内容。 */
function previewIsCurrent(seq: number, payload: ImportPayload): boolean {
  if (seq !== previewSeq || payload.text !== importText.value || payload.source !== source.value) {
    return false
  }
  const pinned = payload.source === 'paste' ? questionCode.value : undefined
  return payload.question_code === pinned
}

async function runPreview(): Promise<void> {
  const seq = ++previewSeq
  previewing.value = true
  lastProblems.value = []
  const payload: ImportPayload = { source: source.value, text: importText.value }
  if (source.value === 'paste') {
    payload.question_code = questionCode.value
  }
  try {
    const result = await previewAnswers(payload)
    if (!previewIsCurrent(seq, payload)) {
      return
    }
    preview.value = result
    await syncQuestionFromPreview()
  } catch (error) {
    if (previewIsCurrent(seq, payload)) {
      preview.value = null
      notifyError(error)
    }
  } finally {
    if (seq === previewSeq) {
      previewing.value = false
    }
  }
}

async function syncQuestionFromPreview(): Promise<void> {
  const codes = preview.value?.question_codes ?? []
  if (!codes.length || codes.includes(questionCode.value)) {
    return
  }
  // 文件里的题号与当前选择不一致：选中项必须跟着换，答卷列表也得重取，
  // 否则第二步还挂着上一道题的答卷，发起评分会被服务端拒绝。
  questionCode.value = codes[0]
  selectedIds.value = []
  await loadAnswers()
}

async function confirmImport(): Promise<void> {
  const current = preview.value
  if (!current?.ok) {
    return
  }
  importing.value = true
  try {
    const payload: ImportPayload = {
      source: source.value,
      text: importText.value,
      skip_duplicates: skipDuplicates.value,
    }
    if (source.value === 'paste') {
      payload.question_code = questionCode.value
    }
    const result = await importAnswers(payload)
    if (result.error) {
      notifyError(result.error)
      return
    }
    importedCount.value = result.imported
    lastProblems.value = result.problems ?? []
    notifySuccess(
      `已导入 ${result.imported} 份，跳过 ${result.skipped_duplicates} 份重复；学号已丢弃，只保留脱敏编号`,
    )
    // 预览与确认必须对应同一段文本：导入后清掉预览，避免误以为还能再确认一次
    preview.value = null
    await loadAnswers()
  } catch (error) {
    notifyError(error)
  } finally {
    importing.value = false
  }
}

async function onFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 清空 value：否则连续选同一个文件不会触发 change
  input.value = ''
  if (!file) {
    return
  }
  if (file.size > MAX_FILE_BYTES) {
    notifyError('文件超过 2 MB，建议按题拆成多个文件再导入')
    return
  }
  try {
    importText.value = await file.text()
    // 上传后直接给预览：教师选的是一整个班的文件，不该再猜一次“要不要点预览”
    if (source.value === 'csv' && importText.value.trim()) {
      await runPreview()
    }
  } catch {
    notifyError('读取文件失败，请确认文件是 UTF-8 编码的 CSV 或文本')
  }
}

// ---------------------------------------------------------------- 选题与勾选
async function onQuestionChange(): Promise<void> {
  selectedIds.value = []
  // 只有单份粘贴的题号会改变提交内容；CSV 的题号来自文件，换评分对象不应丢预览
  if (source.value === 'paste') {
    preview.value = null
  }
  // 不清 task：地址栏里仍带着任务号，清掉页面却还留着一个打不开的链接更糟。
  // 任务卡片自己标了题号，新任务开始时会覆盖它。
  await loadAnswers()
}

function toggle(answerId: number): void {
  const index = selectedIds.value.indexOf(answerId)
  if (index >= 0) {
    selectedIds.value.splice(index, 1)
  } else {
    selectedIds.value.push(answerId)
  }
}

function selectAll(): void {
  selectedIds.value = answers.value.map((item) => item.answer_id)
}

function clearSelection(): void {
  selectedIds.value = []
}

// ---------------------------------------------------------------- 任务
let pollTimer: ReturnType<typeof setTimeout> | null = null
let disposed = false
const pollError = ref('')

function stopPolling(): void {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePolling(force = false): void {
  if (disposed) {
    return
  }
  stopPolling()
  const current = task.value
  if (!current || (!force && !['pending', 'running'].includes(current.status))) {
    return
  }
  pollError.value = ''
  pollTimer = setTimeout(async () => {
    pollTimer = null
    const active = task.value
    if (!active) {
      return
    }
    try {
      task.value = await getTask(active.task_id)
    } catch (error) {
      // 不静默停在“评分中”：明确告诉教师自动刷新停了，并留一个手动继续的入口
      pollError.value = notifyError(error)
      return
    }
    schedulePolling()
  }, POLL_MS)
}

async function resumePolling(): Promise<void> {
  const current = task.value
  if (!current) {
    return
  }
  try {
    task.value = await getTask(current.task_id)
    pollError.value = ''
    schedulePolling()
  } catch (error) {
    pollError.value = notifyError(error)
  }
}

async function startTask(): Promise<void> {
  if (!questionCode.value || !selectedIds.value.length) {
    return
  }
  starting.value = true
  try {
    const created = await createTask({
      question_code: questionCode.value,
      answer_ids: [...selectedIds.value],
      samples: samples.value,
    })
    task.value = created
    // 先恢复轮询再改地址栏：导航失败时进度也不该停在原地
    if (!disposed) {
      schedulePolling()
    }
    // 任务号进地址栏：刷新或换页回来还能接上进度
    await router.replace({ query: { ...route.query, task: String(created.task_id) } })
  } catch (error) {
    notifyError(error)
  } finally {
    starting.value = false
  }
}

async function retryFailed(): Promise<void> {
  await retryWith([])
}

async function retryOne(answerId: number): Promise<void> {
  await retryWith([answerId])
}

async function retryWith(answerIds: number[]): Promise<void> {
  const current = task.value
  if (!current) {
    return
  }
  try {
    task.value = await retryTask(current.task_id, answerIds)
    if (!disposed) {
      // 重试响应可能还是 interrupted（执行器线程刚启动）：强制先读一次，
      // 不能因为回显状态没变成 running 就不轮询。
      schedulePolling(true)
    }
  } catch (error) {
    notifyError(error)
  }
}

function goReview(): void {
  const current = task.value
  if (!current) {
    return
  }
  void router.push({ name: 'review', query: { question_code: current.question_code } })
}

// ---------------------------------------------------------------- 生命周期
watch(importText, () => {
  // 文本一改，旧预览就过期了：它描述的已经不是要提交的内容
  preview.value = null
})

watch(source, () => {
  // CSV 的预览不能拿去确认一次“单份粘贴”
  preview.value = null
})

// 地址栏里的任务号是唯一入口：刷新、从复核页返回或前进后退都以它为准
watch(
  () => route.query.task,
  async (value) => {
    const taskId = parseTaskId(value)
    if (taskId === null) {
      // 后退到没有任务号的地址：旧任务卡片不该继续挂着
      stopPolling()
      task.value = null
      pollError.value = ''
      return
    }
    if (taskId === task.value?.task_id) {
      return
    }
    if (await loadTask(taskId)) {
      schedulePolling()
    }
  },
)

onMounted(bootstrap)

onBeforeUnmount(() => {
  disposed = true
  stopPolling()
})
</script>

<template>
  <div class="grading">
    <div class="page-head">
      <h2>导入与评分</h2>
      <p class="lead">
        先导入答卷，再勾选要评的几份。评分在后台执行，可以留在这个页面看进度；
        一份失败不会拖垮整批，失败项可以单独重试。
      </p>
    </div>

    <el-alert
      v-if="!rubricsLoading && !rubrics.length"
      type="warning"
      :closable="false"
      data-test="no-rubrics"
    >
      <template #title>
        还没有评分标准：<router-link to="/rubrics/new">先保存一份标准</router-link>，
        导入的题号必须已经存在。
      </template>
    </el-alert>

    <el-card class="section" data-test="import-card">
      <template #header>
        <div class="card-head">
          <span>1 导入答卷</span>
          <el-tag v-if="importedCount" type="success" size="small" data-test="imported-count">
            本次已导入 {{ importedCount }} 份
          </el-tag>
        </div>
      </template>

      <el-tabs v-model="source" class="source-tabs">
        <el-tab-pane name="csv">
          <template #label><span data-test="tab-csv">批量 CSV</span></template>
        </el-tab-pane>
        <el-tab-pane name="paste">
          <template #label><span data-test="tab-paste">单份粘贴</span></template>
        </el-tab-pane>
      </el-tabs>

      <el-form label-width="88px" class="import-form">
        <el-form-item v-if="source === 'paste'" label="题号">
          <el-select
            v-model="questionCode"
            filterable
            placeholder="选择这份答案属于哪道题"
            data-test="paste-question"
            @change="onQuestionChange"
          >
            <el-option
              v-for="option in questionOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item v-if="source === 'csv'" label="文件">
          <div class="file-row">
            <input
              type="file"
              accept=".csv,.txt,text/csv,text/plain"
              data-test="file"
              @change="onFile"
            />
            <span class="hint">
              需为 UTF-8；2 列（题号,答案）或 3 列（学号,题号,答案），可带表头
            </span>
          </div>
        </el-form-item>

        <el-form-item label="内容">
          <el-input
            v-model="importText"
            type="textarea"
            :rows="6"
            data-test="import-text"
            :placeholder="
              source === 'csv'
                ? '学号,题号,答案\n2024001,Q-01,客户端发送 SYN……'
                : '把这一份答案粘贴到这里'
            "
          />
        </el-form-item>
      </el-form>

      <div class="actions">
        <el-button
          :loading="previewing"
          :disabled="!importText.trim()"
          data-test="preview"
          @click="runPreview"
        >
          预览
        </el-button>
        <el-checkbox v-model="skipDuplicates" data-test="skip-duplicates">
          跳过重复
        </el-checkbox>
        <el-button
          type="primary"
          :loading="importing"
          :disabled="!canImport"
          data-test="confirm-import"
          @click="confirmImport"
        >
          确认导入
        </el-button>
        <span class="hint">学号只在文件里作占位，入库前丢弃</span>
      </div>

      <el-alert
        v-if="lastProblems.length"
        type="warning"
        :closable="false"
        class="problems"
        data-test="import-problems"
      >
        <template #title>有 {{ lastProblems.length }} 行没有导入（只报行号与原因）</template>
        <ul class="problem-list">
          <li v-for="problem in lastProblems" :key="problem.line">
            第 {{ problem.line }} 行：{{ problem.reason }}
          </li>
        </ul>
      </el-alert>

      <PreviewPanel v-if="preview" :preview="preview" />
    </el-card>

    <el-card class="section" data-test="grade-card">
      <template #header>
        <div class="card-head">
          <span>2 选择答卷并发起评分</span>
          <span class="muted">评分任务会固定当前最新的评分标准</span>
        </div>
      </template>

      <div class="toolbar">
        <el-select
          v-model="questionCode"
          filterable
          placeholder="选择题号"
          class="question-select"
          data-test="question"
          @change="onQuestionChange"
        >
          <el-option
            v-for="option in questionOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button :loading="answersLoading" data-test="refresh-answers" @click="loadAnswers">
          刷新答卷
        </el-button>
        <span class="muted">
          共 {{ answerTotal }} 份<template v-if="answerTotal > answers.length">
            （列表只显示前 {{ answers.length }} 份）</template>
        </span>
      </div>

      <el-table
        v-loading="answersLoading"
        :data="answers"
        size="small"
        row-key="answer_id"
        data-test="answer-table"
        max-height="320"
      >
        <el-table-column width="48">
          <template #default="{ row }">
            <el-checkbox
              :model-value="selectedIds.includes(row.answer_id)"
              data-test="pick-answer"
              @change="toggle(row.answer_id)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="pseudo_id" label="脱敏编号" width="108" />
        <el-table-column prop="source" label="来源" width="88" />
        <el-table-column prop="length" label="字数" width="80" />
        <el-table-column prop="created_at" label="导入时间" min-width="180" />
      </el-table>

      <el-empty
        v-if="!answersLoading && !answers.length"
        description="这道题还没有答卷：先在上一步导入，或换一道题"
        data-test="no-answers"
      />

      <div class="actions">
        <el-button text data-test="select-all" @click="selectAll">全选</el-button>
        <el-button text data-test="clear-selection" @click="clearSelection">清空</el-button>
        <span class="muted">已选 {{ selectedIds.length }} 份</span>
        <span class="samples">
          每份采样
          <el-input-number v-model="samples" :min="1" :max="5" size="small" data-test="samples" />
          次
        </span>
        <span class="hint">次数越多越稳，成本与耗时也按次增长</span>
        <el-button
          type="primary"
          :loading="starting"
          :disabled="!selectedIds.length || !questionCode"
          data-test="start-task"
          @click="startTask"
        >
          开始评分（{{ selectedIds.length }} 份）
        </el-button>
      </div>
    </el-card>

    <TaskProgressPanel
      v-if="task"
      :task="task"
      :poll-error="pollError"
      @retry="retryFailed"
      @retry-one="retryOne"
      @review="goReview"
      @resume="resumePolling"
    />
  </div>
</template>

<style scoped>
.grading {
  max-width: 1180px;
}

.page-head h2 {
  margin: 0 0 8px;
}

.lead {
  margin: 0 0 16px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}

.section {
  margin-bottom: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.source-tabs {
  margin-top: -8px;
}

.import-form {
  max-width: 880px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.problems {
  margin-top: 12px;
}

.problem-list {
  margin: 6px 0 0;
  padding-left: 20px;
  line-height: 1.7;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.question-select {
  min-width: 320px;
}

.samples {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.muted {
  color: var(--el-text-color-secondary);
}

@media (max-width: 720px) {
  .question-select {
    min-width: 100%;
  }
}
</style>
