<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getResult,
  reviewResult,
  type ResultDetail,
  type ResultDeduction,
  type ResultItem,
  type ResultSpan,
} from '../api/results'
import { notifyError, notifySuccess } from '../feedback'

const route = useRoute()
const router = useRouter()

const detail = ref<ResultDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const activeSpan = ref<ResultSpan | null>(null)
const finalScore = ref(0)
const comment = ref('')
const saving = ref(false)

const aggregateId = computed(() => Number(route.params.id))
const maxScore = computed(() => detail.value?.score.max ?? 0)
const canReview = computed(() => Boolean(detail.value?.current && !detail.value.review))
const scoreValid = computed(
  () =>
    Number.isFinite(finalScore.value) &&
    finalScore.value >= 0 &&
    finalScore.value <= maxScore.value,
)
const scoreChanged = computed(
  () => detail.value !== null && Math.abs(finalScore.value - detail.value.score.raw) > 1e-9,
)
const canOverride = computed(
  () =>
    canReview.value &&
    scoreValid.value &&
    scoreChanged.value &&
    comment.value.trim().length > 0,
)

const overrideHint = computed(() => {
  if (!canReview.value) {
    return ''
  }
  if (!comment.value.trim()) {
    return '改分必须填写意见'
  }
  if (!scoreValid.value) {
    return `分数需在 0–${maxScore.value} 之间`
  }
  if (!scoreChanged.value) {
    return '分数没有变化；如果要采纳原分，请用左侧按钮'
  }
  return ''
})

const answerParts = computed(() => {
  const text = detail.value?.answer.text ?? ''
  const span = activeSpan.value
  if (!span || span.start < 0 || span.end > text.length || span.start >= span.end) {
    return [{ text, marked: false }]
  }
  const parts = []
  if (span.start > 0) {
    parts.push({ text: text.slice(0, span.start), marked: false })
  }
  parts.push({ text: text.slice(span.start, span.end), marked: true })
  if (span.end < text.length) {
    parts.push({ text: text.slice(span.end), marked: false })
  }
  return parts
})

const supersededLink = computed(() =>
  detail.value?.superseded_by ? `/review/${detail.value.superseded_by}` : '',
)

function routeLabel(value: 'auto' | 'human' | null | undefined): string {
  if (value === 'auto') {
    return '自动通过'
  }
  return value === 'human' ? '人工复核' : '—'
}

function decisionLabel(value: 'accept' | 'override' | null | undefined): string {
  if (value === 'accept') {
    return '采纳原分'
  }
  return value === 'override' ? '改分' : ''
}

function levelsText(levels: Record<string, number>): string {
  return Object.entries(levels)
    .map(([id, level]) => `${id}:${level}`)
    .join('  ')
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const result = await getResult(aggregateId.value)
    detail.value = result
    finalScore.value = result.score.final ?? result.score.raw
    activeSpan.value = null
  } catch (error) {
    loadError.value = notifyError(error)
  } finally {
    loading.value = false
  }
}

async function showEvidence(entry: ResultItem | ResultDeduction): Promise<void> {
  if (!entry.span) {
    return
  }
  activeSpan.value = entry.span
  await nextTick()
  document.querySelector('[data-test="evidence-mark"]')?.scrollIntoView?.({ block: 'center' })
}

async function submit(decision: 'accept' | 'override'): Promise<void> {
  const current = detail.value
  if (!current || !canReview.value || saving.value) {
    return
  }
  if (decision === 'override' && !canOverride.value) {
    return
  }
  saving.value = true
  try {
    const payload =
      decision === 'accept'
        ? { decision, comment: comment.value.trim() }
        : { decision, final_score: finalScore.value, comment: comment.value.trim() }
    const record = await reviewResult(current.aggregate_id, payload)
    detail.value = {
      ...current,
      score: { ...current.score, final: record.final_score },
      review: record,
    }
    comment.value = ''
    notifySuccess(`已定稿：${record.final_score} 分（${decisionLabel(record.decision)}）`)
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

function back(): void {
  // 从带题号筛选的队列进来就保持筛选；直接打开详情则回到全部题目
  const filter = route.query.question_code
  void router.push({
    name: 'review',
    query: typeof filter === 'string' ? { question_code: filter } : {},
  })
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="review-detail">
    <div class="page-head">
      <el-button link data-test="back-to-queue" @click="back">← 返回复核队列</el-button>
      <div v-if="detail" class="title-row">
        <h2>{{ detail.answer.pseudo_id }} · {{ detail.rubric.question_id }}</h2>
        <el-tag v-if="detail.review" type="success" data-test="finalized-tag">已定稿</el-tag>
        <el-tag v-else-if="!detail.current" type="info">已被取代</el-tag>
        <el-tag v-else-if="detail.score.route === 'auto'" type="success">待定稿</el-tag>
        <el-tag v-else type="warning">待复核</el-tag>
      </div>
      <p v-if="detail" class="lead">{{ detail.rubric.stem }}</p>
    </div>

    <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" data-test="load-error">
      <el-button link type="primary" @click="load">重试</el-button>
    </el-alert>

    <template v-if="detail">
      <el-alert
        v-if="!detail.current"
        type="warning"
        :closable="false"
        class="section"
        data-test="superseded"
      >
        <template #title>
          这条结论已被后来的重评取代（当前结论 #{{ detail.superseded_by }}），不能定稿。
          <router-link data-test="current-result-link" :to="supersededLink">
            打开当前结论
          </router-link>
        </template>
      </el-alert>
      <el-alert
        v-if="detail.answer.truncated"
        type="warning"
        :closable="false"
        class="section"
        title="答卷过长，评分时被截断；原文高亮仍按完整文本定位"
        data-test="truncated"
      />

      <el-row :gutter="16">
        <el-col :xs="24" :lg="15">
          <el-card class="section" data-test="answer-card">
            <template #header>答卷原文</template>
            <div class="answer-text" data-test="answer-text">
              <template v-for="(part, index) in answerParts" :key="index">
                <mark v-if="part.marked" data-test="evidence-mark">{{ part.text }}</mark>
                <span v-else>{{ part.text }}</span>
              </template>
            </div>
            <p class="hint">点击下方证据，可在原文中高亮模型引用的片段。</p>
          </el-card>

          <el-card class="section" data-test="items-card">
            <template #header>逐条判定</template>
            <el-table :data="detail.items" size="small" data-test="item-table">
              <el-table-column prop="id" label="点" width="56" />
              <el-table-column prop="desc" label="采分点" min-width="180" show-overflow-tooltip />
              <el-table-column prop="weight" label="权重" width="64" />
              <el-table-column prop="level" label="等级" width="64" />
              <el-table-column prop="score" label="得分" width="64" />
              <el-table-column label="采样等级" width="100">
                <template #default="{ row }">
                  {{ row.sample_levels.map((value: number | null) => value ?? '—').join(' / ') }}
                </template>
              </el-table-column>
              <el-table-column label="证据" min-width="220">
                <template #default="{ row }">
                  <el-button
                    link
                    type="primary"
                    :disabled="!row.span"
                    data-test="show-evidence"
                    :title="row.reason"
                    @click="showEvidence(row)"
                  >
                    {{ row.evidence || '未定位' }}
                  </el-button>
                </template>
              </el-table-column>
              <el-table-column label="标记" width="120">
                <template #default="{ row }">
                  <el-tag v-if="row.disagree" type="warning" size="small">采样分歧</el-tag>
                  <el-tag v-else-if="!row.grounded" type="danger" size="small">未接地</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card v-if="detail.deductions.length" class="section" data-test="deductions-card">
            <template #header>扣分项</template>
            <el-table :data="detail.deductions" size="small" data-test="deduction-table">
              <el-table-column prop="id" label="点" width="56" />
              <el-table-column prop="desc" label="扣分项" min-width="200" show-overflow-tooltip />
              <el-table-column prop="penalty" label="分值" width="64" />
              <el-table-column prop="cap" label="封顶" width="64" />
              <el-table-column label="生效" width="72">
                <template #default="{ row }">
                  <el-tag :type="row.applied ? 'danger' : 'info'" size="small">
                    {{ row.applied ? '生效' : '未生效' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="命中" width="72">
                <template #default="{ row }">{{ row.hit_samples }} 次</template>
              </el-table-column>
              <el-table-column label="证据" min-width="180">
                <template #default="{ row }">
                  <el-button
                    link
                    type="primary"
                    :disabled="!row.span"
                    data-test="show-evidence"
                    @click="showEvidence(row)"
                  >
                    {{ row.evidence || '未定位' }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card class="section" data-test="samples-card">
            <template #header>采样分歧</template>
            <el-table :data="detail.samples" size="small" data-test="sample-table">
              <el-table-column prop="run_id" label="采样" width="72" />
              <el-table-column prop="score" label="得分" width="80" />
              <el-table-column label="等级">
                <template #default="{ row }">{{ levelsText(row.levels) }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>

        <el-col :xs="24" :lg="9">
          <el-card class="section" data-test="score-card">
            <template #header>结论</template>
            <div class="score-line">
              <strong>{{ detail.score.raw }}</strong>
              <span class="muted"> / {{ detail.score.max }}</span>
            </div>
            <el-tag :type="detail.score.route === 'auto' ? 'success' : 'warning'" size="small">
              {{ routeLabel(detail.score.route) }}
            </el-tag>
            <el-descriptions :column="1" border size="small" class="score-meta">
              <el-descriptions-item label="置信度">
                {{ Math.round(detail.score.confidence * 100) }}%
              </el-descriptions-item>
              <el-descriptions-item label="接地率">
                {{ Math.round(detail.score.grounding_rate * 100) }}%
              </el-descriptions-item>
              <el-descriptions-item label="采样极差">{{ detail.score.total_range }}</el-descriptions-item>
              <el-descriptions-item label="风险">{{ detail.score.risk.toFixed(2) }}</el-descriptions-item>
            </el-descriptions>
            <div v-if="detail.score.reasons.length" class="reasons" data-test="reasons">
              <el-tag
                v-for="reason in detail.score.reasons"
                :key="reason"
                type="warning"
                size="small"
                effect="plain"
              >
                {{ reason }}
              </el-tag>
            </div>
          </el-card>

          <el-card class="section" data-test="review-card">
            <template #header>定稿</template>

            <el-alert
              v-if="detail.review"
              type="success"
              :closable="false"
              data-test="finalized"
            >
              <template #title>
                已定稿：{{ detail.review.final_score }} 分（{{
                  decisionLabel(detail.review.decision)
                }}）
              </template>
              <p v-if="detail.review.comment" class="review-comment">
                {{ detail.review.comment }}
              </p>
              <p class="muted">复核人 {{ detail.review.reviewer_id }}</p>
            </el-alert>

            <el-alert
              v-else-if="!detail.current"
              type="info"
              :closable="false"
              title="这不是当前结论，不能定稿"
              data-test="not-current"
            />

            <template v-else>
              <el-form label-position="top">
                <el-form-item label="改分（满分 {{ maxScore }}）">
                  <el-input-number
                    v-model="finalScore"
                    :min="0"
                    :max="maxScore"
                    :step="0.5"
                    data-test="final-score"
                  />
                </el-form-item>
                <el-form-item label="意见">
                  <el-input
                    v-model="comment"
                    type="textarea"
                    :rows="3"
                    data-test="review-comment"
                    placeholder="改分必须写理由；采纳原分可以留空"
                  />
                </el-form-item>
              </el-form>
              <p v-if="overrideHint" class="hint" data-test="override-hint">
                {{ overrideHint }}
              </p>
              <div class="actions">
                <el-button :loading="saving" data-test="accept-score" @click="submit('accept')">
                  采纳原分（{{ detail.score.raw }}）
                </el-button>
                <el-button
                  type="primary"
                  :loading="saving"
                  :disabled="!canOverride"
                  data-test="override-score"
                  @click="submit('override')"
                >
                  改分并定稿
                </el-button>
              </div>
            </template>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.review-detail {
  max-width: 1320px;
  min-height: 320px;
}

.page-head {
  margin-bottom: 16px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.title-row h2 {
  margin: 0;
}

.lead {
  margin: 8px 0 0;
  color: var(--el-text-color-regular);
}

.section {
  margin-bottom: 16px;
}

.answer-text {
  line-height: 1.9;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 15px;
}

.answer-text mark {
  background: #fff3bf;
  padding: 0 2px;
  border-radius: 2px;
}

.hint {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.score-line {
  font-size: 32px;
  line-height: 1.2;
  margin-bottom: 8px;
}

.score-meta {
  margin-top: 12px;
}

.reasons {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.review-comment {
  margin: 8px 0 0;
  white-space: pre-wrap;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
