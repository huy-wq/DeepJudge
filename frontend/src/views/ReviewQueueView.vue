<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  batchAccept,
  listResults,
  type ResultList,
  type ResultRow,
} from '../api/results'
import { notifyError, notifySuccess } from '../feedback'

const route = useRoute()
const router = useRouter()

const payload = ref<ResultList | null>(null)
const loading = ref(false)
const failed = ref(false)
const mode = ref<'human' | 'auto'>('human')
const selected = ref<number[]>([])
const confirming = ref(false)
const accepting = ref(false)

const questionCode = computed(() =>
  typeof route.query.question_code === 'string' ? route.query.question_code : '',
)

const isAuto = computed(() => mode.value === 'auto')

const rows = computed(() => payload.value?.items ?? [])

let requestSeq = 0

async function load(): Promise<void> {
  // 连续改筛选条件时，只认最后一次发出的请求
  const seq = ++requestSeq
  loading.value = true
  failed.value = false
  try {
    const result = await listResults({
      route: mode.value,
      pending: true,
      question_code: questionCode.value || undefined,
    })
    if (seq === requestSeq) {
      payload.value = result
      const available = new Set(result.items.map((row) => row.aggregate_id))
      selected.value = selected.value.filter((id) => available.has(id))
    }
  } catch (error) {
    if (seq === requestSeq) {
      failed.value = true
      notifyError(error)
    }
  } finally {
    if (seq === requestSeq) {
      loading.value = false
    }
  }
}

watch(
  [questionCode, mode],
  () => {
    selected.value = []
    void load()
  },
  { immediate: true },
)

function clearFilter(): void {
  void router.replace({ name: 'review' })
}

function toggle(aggregateId: number): void {
  const index = selected.value.indexOf(aggregateId)
  if (index >= 0) {
    selected.value.splice(index, 1)
  } else {
    selected.value.push(aggregateId)
  }
}

function selectAll(): void {
  selected.value = rows.value.map((row) => row.aggregate_id)
}

function clearSelection(): void {
  selected.value = []
}

function openRow(row: ResultRow): void {
  const query = questionCode.value ? { question_code: questionCode.value } : {}
  void router.push({ name: 'review-detail', params: { id: row.aggregate_id }, query })
}

async function submitBatch(): Promise<void> {
  if (!selected.value.length || accepting.value) {
    return
  }
  accepting.value = true
  try {
    const result = await batchAccept([...selected.value])
    notifySuccess(`已通过 ${result.reviews.length} 份自动结果`)
    confirming.value = false
    selected.value = []
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    accepting.value = false
  }
}
</script>

<template>
  <div class="review-queue">
    <div class="toolbar">
      <div>
        <h2>复核队列</h2>
        <p class="lead">
          待人工复核按风险从高到低排；自动通过的一批集中定稿。批量通过只接受自动路由的结果，
          混入待复核的会整批拒绝。
        </p>
      </div>
      <div class="toolbar-actions">
        <el-button :loading="loading" data-test="refresh-review" @click="load">刷新</el-button>
      </div>
    </div>

    <el-card>
      <el-tabs v-model="mode" class="mode-tabs">
        <el-tab-pane name="human">
          <template #label><span data-test="tab-human">待人工复核</span></template>
        </el-tab-pane>
        <el-tab-pane name="auto">
          <template #label><span data-test="tab-auto">自动通过（待定稿）</span></template>
        </el-tab-pane>
      </el-tabs>

      <div class="summary">
        <el-tag :type="isAuto ? 'success' : 'warning'" data-test="review-total">
          {{ payload?.total ?? 0 }} 份待处理
        </el-tag>
        <el-tag v-if="questionCode" closable data-test="review-filter" @close="clearFilter">
          题号 {{ questionCode }}
        </el-tag>
        <span v-if="payload && payload.total > payload.items.length" class="muted">
          当前只显示前 {{ payload.items.length }} 份
        </span>
      </div>

      <div v-if="isAuto" class="batch-bar">
        <el-button text data-test="select-all-results" @click="selectAll">全选</el-button>
        <el-button text data-test="clear-results" @click="clearSelection">清空</el-button>
        <span class="muted">已选 {{ selected.length }} 份</span>
        <el-button
          type="primary"
          :disabled="!selected.length"
          data-test="batch-accept"
          @click="confirming = true"
        >
          批量通过（{{ selected.length }} 份）
        </el-button>
      </div>

      <el-table
        v-loading="loading"
        :data="rows"
        size="small"
        row-key="aggregate_id"
        data-test="review-table"
        @row-click="openRow"
      >
        <el-table-column v-if="isAuto" width="48">
          <template #default="{ row }">
            <el-checkbox
              :model-value="selected.includes(row.aggregate_id)"
              data-test="pick-result"
              @click.stop
              @change="toggle(row.aggregate_id)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="question_code" label="题号" width="92" />
        <el-table-column prop="pseudo_id" label="答卷" width="108" />
        <el-table-column label="模型分" width="104">
          <template #default="{ row }">
            {{ row.raw_score }} / {{ row.max_score }}
          </template>
        </el-table-column>
        <el-table-column label="风险" width="80">
          <template #default="{ row }">{{ row.risk.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="置信度" width="88">
          <template #default="{ row }">{{ Math.round(row.confidence * 100) }}%</template>
        </el-table-column>
        <el-table-column label="原因" min-width="300">
          <template #default="{ row }">
            <div v-if="row.reasons.length" class="reasons">
              <el-tag
                v-for="reason in row.reasons"
                :key="reason"
                type="warning"
                size="small"
                effect="plain"
              >
                {{ reason }}
              </el-tag>
            </div>
            <span v-else class="muted">高置信自动通过，待集中定稿</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="评分时间" min-width="180" />
        <el-table-column label="操作" width="88">
          <template #default="{ row }">
            <el-button link type="primary" data-test="open-result" @click.stop="openRow(row)">
              打开
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        v-if="failed"
        type="error"
        :closable="false"
        title="队列加载失败，请点“刷新”重试"
        data-test="review-error"
      />
      <el-empty
        v-else-if="!loading && !rows.length"
        :description="
          isAuto ? '没有等待定稿的自动结果' : '队列是空的：没有需要人工确认的结论'
        "
        data-test="review-empty"
      />
    </el-card>

    <el-dialog
      v-model="confirming"
      title="确认通过这批结果"
      width="460px"
      :close-on-click-modal="false"
    >
      <p>
        将把选中的 {{ selected.length }} 份<strong>自动通过</strong>
        结果按模型原分定稿。定稿后不能重评，也不能再修改标准影响它们。
      </p>
      <template #footer>
        <el-button data-test="batch-cancel" @click="confirming = false">取消</el-button>
        <el-button
          type="primary"
          :loading="accepting"
          data-test="batch-confirm"
          @click="submitBatch"
        >
          确认通过
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.review-queue {
  max-width: 1180px;
}

.toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.toolbar h2 {
  margin: 0 0 8px;
}

.lead {
  margin: 0;
  max-width: 760px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}

.mode-tabs {
  margin-top: -8px;
}

.summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.reasons {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.muted {
  color: var(--el-text-color-secondary);
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
