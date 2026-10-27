<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { listResults, type ResultList } from '../api/results'
import { notifyError } from '../feedback'

const route = useRoute()
const router = useRouter()

const payload = ref<ResultList | null>(null)
const loading = ref(false)
const failed = ref(false)

const questionCode = computed(() =>
  typeof route.query.question_code === 'string' ? route.query.question_code : '',
)

let requestSeq = 0

async function load(): Promise<void> {
  // 连续改筛选条件时，只认最后一次发出的请求
  const seq = ++requestSeq
  loading.value = true
  failed.value = false
  try {
    const result = await listResults({
      route: 'human',
      pending: true,
      question_code: questionCode.value || undefined,
    })
    if (seq === requestSeq) {
      payload.value = result
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

function clearFilter(): void {
  void router.replace({ name: 'review' })
}

// 从任务页带着题号进来时，查询参数变化要重新取；直接挂载时也会先跑一次
watch(questionCode, load, { immediate: true })
</script>

<template>
  <div class="review-queue">
    <div class="toolbar">
      <div>
        <h2>复核队列</h2>
        <p class="lead">
          只列每份答卷最新的、需要人工确认的结论；自动通过的不在这里。按风险从高到低排，
          先看最可疑的几份。
        </p>
      </div>
      <div class="toolbar-actions">
        <el-button :loading="loading" data-test="refresh-review" @click="load">刷新</el-button>
      </div>
    </div>

    <el-card>
      <div class="summary">
        <el-tag type="warning" data-test="review-total">
          {{ payload?.total ?? 0 }} 份待处理
        </el-tag>
        <el-tag v-if="questionCode" closable data-test="review-filter" @close="clearFilter">
          题号 {{ questionCode }}
        </el-tag>
        <span v-if="payload && payload.total > payload.items.length" class="muted">
          当前只显示前 {{ payload.items.length }} 份
        </span>
      </div>

      <el-table
        v-loading="loading"
        :data="payload?.items ?? []"
        size="small"
        row-key="aggregate_id"
        data-test="review-table"
      >
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
        <el-table-column label="转人工原因" min-width="320">
          <template #default="{ row }">
            <div class="reasons">
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
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="评分时间" min-width="180" />
      </el-table>

      <el-alert
        v-if="failed"
        type="error"
        :closable="false"
        title="队列加载失败，请点“刷新”重试"
        data-test="review-error"
      />
      <el-empty
        v-else-if="!loading && !(payload?.items.length ?? 0)"
        description="队列是空的：没有需要人工确认的结论"
        data-test="review-empty"
      />
    </el-card>
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

.summary {
  display: flex;
  align-items: center;
  gap: 8px;
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
</style>
