<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { listRubrics, type RubricListItem } from '../api/rubrics'
import { notifyError } from '../feedback'

const router = useRouter()
const rows = ref<RubricListItem[]>([])
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = (await listRubrics()).items
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function open(row: RubricListItem): void {
  void router.push(`/rubrics/${row.rubric_id}`)
}

function create(): void {
  void router.push('/rubrics/new')
}

onMounted(load)
</script>

<template>
  <div class="rubric-list">
    <div class="toolbar">
      <h2>评分标准</h2>
      <div class="toolbar-actions">
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" data-test="new-rubric" @click="create">新建标准</el-button>
      </div>
    </div>

    <el-card>
      <el-table
        v-loading="loading"
        :data="rows"
        data-test="rubric-table"
        row-key="rubric_id"
        @row-click="open"
      >
        <el-table-column prop="question_code" label="题号" width="96" />
        <el-table-column prop="stem" label="题干" min-width="240" show-overflow-tooltip />
        <el-table-column prop="total_score" label="满分" width="80" />
        <el-table-column prop="item_count" label="采分点" width="84" />
        <el-table-column prop="version" label="版本" width="72" />
        <el-table-column label="操作" width="92">
          <template #default="{ row }">
            <el-button link type="primary" data-test="open-rubric" @click.stop="open(row)">
              打开
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!loading && !rows.length"
        description="还没有保存过评分标准：点右上角新建一份"
        data-test="empty"
      />
    </el-card>
  </div>
</template>

<style scoped>
.rubric-list {
  max-width: 1100px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.toolbar h2 {
  margin: 0;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
