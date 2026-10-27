<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import {
  composeSummary,
  getRubric,
  generateRubric,
  issueKey,
  iterateRubric,
  saveRubric,
  validateRubric,
  type GeneratedRubric,
  type RubricChange,
  type RubricData,
  type RubricIssue,
  type RubricRecord,
} from '../api/rubrics'
import ChecklistPanel from '../components/rubrics/ChecklistPanel.vue'
import PreviewTable from '../components/rubrics/PreviewTable.vue'
import { errorMessage, notifyError, notifySuccess } from '../feedback'

const route = useRoute()
const router = useRouter()

const rubric = ref<RubricData | null>(null)
const summary = ref('')
const issues = ref<RubricIssue[]>([])
const acknowledged = ref<Set<string>>(new Set())
const changes = ref<RubricChange[]>([])

const questionCode = ref('Q-01')
const stem = ref('')
const totalScore = ref(10)
const referenceAnswer = ref('')
const standardText = ref('')
const feedback = ref('')
const pendingFeedback = ref('')

const baseId = ref<number | null>(null)
const questionLocked = computed(() => baseId.value !== null)
const busy = computed(() => generating.value || iterating.value || saving.value)

const loading = ref(false)
const generating = ref(false)
const iterating = ref(false)
const validating = ref(false)
const dirty = ref(false)
const validationError = ref<string | null>(null)
const saving = ref(false)
const unsaved = ref(false)

const merged = computed<RubricData | null>(() => {
  if (!rubric.value) {
    return null
  }
  return {
    ...rubric.value,
    question_id: questionCode.value,
    stem: stem.value,
    total_score: totalScore.value,
    reference_answer: referenceAnswer.value,
  }
})

const summaryBaseline = ref('')

function liveKey(): string {
  const source = rubric.value
  if (!source) {
    return ''
  }
  return JSON.stringify({
    question: questionCode.value,
    stem: stem.value,
    total: totalScore.value,
    items: source.items.map((item) => [item.id, item.desc, item.weight]),
  })
}

const summaryText = computed(() => {
  const source = rubric.value
  if (!source) {
    return ''
  }
  if (summary.value && liveKey() === summaryBaseline.value) {
    return summary.value
  }
  // 结构一改，模型摘要里的权重就可能过时；改用按当前结构实时拼的句子
  return composeSummary({
    ...source,
    question_id: questionCode.value,
    stem: stem.value,
    total_score: totalScore.value,
    reference_answer: referenceAnswer.value,
  })
})

const blocking = computed(() => issues.value.filter((issue) => issue.blocking))
const warnings = computed(() => issues.value.filter((issue) => !issue.blocking))
const unacknowledged = computed(() =>
  warnings.value.filter((issue) => !acknowledged.value.has(issueKey(issue))),
)

const canSave = computed(
  () =>
    !!rubric.value &&
    !loading.value &&
    !validating.value &&
    !dirty.value &&
    !validationError.value &&
    !busy.value &&
    !!questionCode.value.trim() &&
    !!stem.value.trim() &&
    !!standardText.value.trim() &&
    blocking.value.length === 0 &&
    unacknowledged.value.length === 0,
)

const saveHint = computed(() => {
  if (!rubric.value) {
    return '先在上方生成草稿，或从列表里打开一份标准'
  }
  if (validationError.value) {
    return `校验失败，保存已禁用：${validationError.value}`
  }
  if (!questionCode.value.trim() || !stem.value.trim()) {
    return '请先填写题号与题干'
  }
  if (!standardText.value.trim()) {
    return '请先填写评分标准原文：保存后它是这些权重由来的依据'
  }
  if (dirty.value || busy.value) {
    return '正在校验或执行其他操作…'
  }
  if (validating.value) {
    return '校验中…'
  }
  if (blocking.value.length) {
    return `有 ${blocking.value.length} 个结构错误，保存已禁用`
  }
  if (unacknowledged.value.length) {
    return `请先勾选确认 ${unacknowledged.value.length} 条提示`
  }
  return '校验通过，可以保存'
})

let timer: ReturnType<typeof setTimeout> | undefined
let validationSeq = 0

function plain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function scheduleValidation(): void {
  if (!rubric.value) {
    return
  }
  // 一有改动就让在途校验作废：否则旧响应会在 200ms 窗口里把清单刷回旧值
  validationSeq += 1
  dirty.value = true
  unsaved.value = true
  validationError.value = null
  if (timer) {
    clearTimeout(timer)
  }
  timer = setTimeout(() => {
    void runValidation()
  }, 200)
}

/** 返回是否拿到了这次校验结果；网络失败时保存应当中止，而不是拿旧清单放行。 */
async function runValidation(): Promise<boolean> {
  const payload = merged.value
  if (!payload) {
    return false
  }
  const seq = ++validationSeq
  validating.value = true
  let succeeded = false
  try {
    const result = await validateRubric(plain(payload))
    if (seq !== validationSeq) {
      return false
    }
    issues.value = result.issues
    validationError.value = null
    pruneAcknowledged()
    succeeded = true
    return true
  } catch (error) {
    if (seq === validationSeq) {
      // 校验失败时清单是旧的：不能继续显示"没有发现问题"，也不能放开保存
      validationError.value = errorMessage(error)
      notifyError(error)
    }
    return false
  } finally {
    if (seq === validationSeq) {
      validating.value = false
      // 只有拿到结果才允许保存；失败时保持"未校验"状态，等下一次成功
      dirty.value = !succeeded
    }
  }
}

function pruneAcknowledged(): void {
  const present = new Set(issues.value.map(issueKey))
  acknowledged.value = new Set(
    [...acknowledged.value].filter((key) => present.has(key)),
  )
}

function toggleAcknowledged(key: string, value: boolean): void {
  const next = new Set(acknowledged.value)
  if (value) {
    next.add(key)
  } else {
    next.delete(key)
  }
  acknowledged.value = next
}

function fillForm(source: RubricData): void {
  questionCode.value = source.question_id
  stem.value = source.stem
  totalScore.value = source.total_score
  referenceAnswer.value = source.reference_answer
}

/** 当前编辑内容的完整指纹：区分"程序化载入"和"教师改动"，避免自己触发校验。 */
function changeKey(): string {
  const source = rubric.value
  if (!source) {
    return ''
  }
  return JSON.stringify({
    question: questionCode.value,
    stem: stem.value,
    total: totalScore.value,
    reference: referenceAnswer.value,
    items: source.items,
    deductions: source.deductions,
  })
}

let lastAppliedKey = ''

function settleFreshIssues(fresh: RubricIssue[]): void {
  issues.value = fresh
  dirty.value = false
  validating.value = false
  validationError.value = null
}

function applyRecord(record: RubricRecord): void {
  rubric.value = record.rubric
  fillForm(record.rubric)
  standardText.value = record.source_text
  summary.value = ''
  settleFreshIssues(record.issues)
  // 上一次的确认不算数：重新打开时让教师再看一眼黄条
  acknowledged.value = new Set()
  changes.value = []
  baseId.value = record.rubric_id
  pendingFeedback.value = ''
  unsaved.value = false
  summaryBaseline.value = liveKey()
  lastAppliedKey = changeKey()
}

function applyDraft(result: GeneratedRubric): void {
  rubric.value = result.rubric
  fillForm(result.rubric)
  summary.value = result.summary
  settleFreshIssues(result.checklist)
  acknowledged.value = new Set()
  changes.value = result.changes ?? []
  unsaved.value = true
  summaryBaseline.value = liveKey()
  lastAppliedKey = changeKey()
}

async function loadExisting(id: number): Promise<void> {
  loading.value = true
  try {
    applyRecord(await getRubric(id))
  } catch (error) {
    notifyError(error)
    await router.replace({ name: 'rubrics' })
  } finally {
    loading.value = false
  }
}

async function generate(): Promise<void> {
  if (busy.value) {
    return
  }
  if (!stem.value.trim() || totalScore.value <= 0 || !standardText.value.trim()) {
    notifyError(new Error('生成前要填好题干、满分与评分标准原文'))
    return
  }
  generating.value = true
  try {
    applyDraft(
      await generateRubric({
        stem: stem.value.trim(),
        total_score: totalScore.value,
        standard_text: standardText.value.trim(),
        reference_answer: referenceAnswer.value,
        question_id: questionCode.value.trim() || 'Q-01',
      }),
    )
    // 重新生成是"从原话重来"，不该把上一次迭代的意见记到这一版上
    pendingFeedback.value = ''
    notifySuccess('草稿已生成，请检查三视图后保存')
  } catch (error) {
    notifyError(error)
  } finally {
    generating.value = false
  }
}

async function iterate(): Promise<void> {
  if (busy.value) {
    return
  }
  if (baseId.value === null) {
    notifyError(new Error('先保存或打开一份标准，才能带意见迭代'))
    return
  }
  if (!feedback.value.trim()) {
    notifyError(new Error('请先写一条修改意见'))
    return
  }
  iterating.value = true
  try {
    applyDraft(await iterateRubric(baseId.value, feedback.value.trim()))
    pendingFeedback.value = feedback.value.trim()
    feedback.value = ''
    notifySuccess('已按意见重新生成，请核对变更清单后保存')
  } catch (error) {
    notifyError(error)
  } finally {
    iterating.value = false
  }
}

async function save(): Promise<void> {
  if (busy.value) {
    return
  }
  const payload = merged.value
  if (!payload) {
    return
  }
  if (timer) {
    clearTimeout(timer)
  }
  if (!standardText.value.trim()) {
    notifyError(new Error('请填写评分标准原文：它是这些权重由来的唯一依据'))
    return
  }

  const validated = await runValidation()
  if (!validated || blocking.value.length || unacknowledged.value.length) {
    return
  }

  saving.value = true
  try {
    const saved = await saveRubric(
      {
        rubric: plain(payload),
        source_text: standardText.value.trim(),
        feedback: pendingFeedback.value,
      },
      baseId.value ?? undefined,
    )
    baseId.value = saved.rubric_id
    pendingFeedback.value = ''
    changes.value = []
    unsaved.value = false
    issues.value = saved.issues
    acknowledged.value = new Set(saved.issues.map(issueKey))
    notifySuccess(
      saved.reused ? `内容没有变化，仍为第 ${saved.version} 版` : `已保存为第 ${saved.version} 版`,
    )
    if (String(route.params.id ?? '') !== String(saved.rubric_id)) {
      await router.replace(`/rubrics/${saved.rubric_id}`)
    }
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

function changeLabel(change: RubricChange): string {
  if (change.change === 'added') {
    return '新增'
  }
  if (change.change === 'removed') {
    return '删除'
  }
  const names: Record<string, string> = {
    desc: '描述',
    weight: '权重',
    levels: '等级',
    must_hit: '必答',
    acceptable: '同义表述',
    penalty: '扣分',
    cap: '封顶',
  }
  return `改了${(change.fields ?? []).map((field) => names[field] ?? field).join('、')}`
}

watch(
  [questionCode, stem, totalScore, referenceAnswer, rubric],
  () => {
    const key = changeKey()
    if (key === lastAppliedKey) {
      return
    }
    lastAppliedKey = key
    scheduleValidation()
  },
  { deep: true },
)

// 离开页面前提醒未保存的改动（生成草稿后也算未保存）
onBeforeRouteLeave(() => {
  if (!unsaved.value) {
    return true
  }
  return window.confirm('还有未保存的改动，确定离开吗？')
})

onMounted(async () => {
  const id = Number(route.params.id)
  if (Number.isFinite(id) && id > 0) {
    await loadExisting(id)
  }
})

onUnmounted(() => {
  if (timer) {
    clearTimeout(timer)
  }
})
</script>

<template>
  <div v-loading="loading" class="rubric-editor" data-test="rubric-editor">
    <section class="left-pane" data-test="left-pane">
      <el-card>
        <template #header>题目与教师原话</template>
        <el-form label-position="top">
          <el-form-item label="题号">
            <el-input
              v-model="questionCode"
              :disabled="questionLocked"
              data-test="question-code"
            />
          </el-form-item>
          <el-form-item label="题干">
            <el-input v-model="stem" type="textarea" :rows="3" data-test="stem" />
          </el-form-item>
          <el-form-item label="满分">
            <el-input-number
              v-model="totalScore"
              :min="0"
              :step="0.5"
              :precision="1"
              controls-position="right"
              data-test="total-score"
            />
          </el-form-item>
          <el-form-item label="参考答案（可选）">
            <el-input
              v-model="referenceAnswer"
              type="textarea"
              :rows="3"
              data-test="reference-answer"
            />
          </el-form-item>
          <el-form-item label="评分标准原文（保存时必填）">
            <el-input
              v-model="standardText"
              type="textarea"
              :rows="5"
              placeholder="例如：三个要点：客户端发 SYN 给 3 分……"
              data-test="standard-text"
            />
          </el-form-item>
          <el-button
            type="primary"
            :loading="generating"
            data-test="generate"
            @click="generate"
          >
            生成草稿
          </el-button>
        </el-form>
      </el-card>

      <el-card v-if="baseId !== null" class="side-card">
        <template #header>带上一版 + 一条意见重新生成</template>
        <el-input
          v-model="feedback"
          type="textarea"
          :rows="3"
          placeholder="例如：P3 拆得太粗，拆成两个采分点"
          data-test="feedback"
        />
        <el-button
          class="iterate-button"
          :loading="iterating"
          :disabled="busy"
          data-test="iterate"
          @click="iterate"
        >
          重新生成
        </el-button>
      </el-card>

      <el-card v-if="changes.length" class="side-card" data-test="changes">
        <template #header>本次迭代改了什么</template>
        <div class="change-tags">
          <el-tag
            v-for="change in changes"
            :key="`${change.type}-${change.id}-${change.change}`"
            :type="
              change.change === 'removed'
                ? 'danger'
                : change.change === 'added'
                  ? 'success'
                  : 'warning'
            "
          >
            {{ change.id }} · {{ changeLabel(change) }}
          </el-tag>
        </div>
      </el-card>
    </section>

    <section class="right-pane">
      <el-card class="block" data-test="summary">
        <template #header>摘要 · 这份标准在考什么</template>
        <p v-if="summaryText" class="summary" data-test="summary-text">{{ summaryText }}</p>
        <p v-else class="muted">生成或打开一份标准后，这里会显示摘要。</p>
      </el-card>

      <el-card class="block" data-test="preview-block">
        <template #header>预览表 · 有没有漏、有没有重复（可直接改）</template>
        <PreviewTable v-if="rubric" :rubric="rubric" />
        <p v-else class="muted">还没有结构化标准。</p>
      </el-card>

      <el-card class="block" data-test="checklist-block">
        <template #header>校验清单 · 合不合法</template>
        <ChecklistPanel
          :issues="issues"
          :validating="validating"
          :error="validationError"
          :acknowledged="acknowledged"
          @toggle="toggleAcknowledged"
        />
        <p v-if="!rubric" class="muted">生成或打开一份标准后，这里会显示校验结果。</p>
      </el-card>

      <div class="save-bar">
        <span class="save-hint" data-test="save-hint">{{ saveHint }}</span>
        <el-button
          type="primary"
          size="large"
          :loading="saving"
          :disabled="!canSave"
          data-test="save"
          @click="save"
        >
          保存
        </el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.rubric-editor {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.left-pane {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 32px);
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.right-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.side-card {
  margin-top: 0;
}

.iterate-button {
  margin-top: 10px;
}

.change-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary {
  margin: 0;
  line-height: 1.7;
}

.muted {
  margin: 0;
  color: var(--el-text-color-secondary);
}

.save-bar {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  box-shadow: 0 6px 18px rgb(0 0 0 / 6%);
}

.save-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

@media (max-width: 960px) {
  .rubric-editor {
    grid-template-columns: 1fr;
  }

  .left-pane {
    position: static;
    max-height: none;
    overflow: visible;
  }
}
</style>
