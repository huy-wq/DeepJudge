<script setup lang="ts">
import { computed } from 'vue'

import type { PreviewResult } from '../../api/answers'

const props = defineProps<{
  preview: PreviewResult
}>()

const duplicateCount = computed(
  () => props.preview.rows.filter((row) => row.duplicate).length,
)
</script>

<template>
  <el-alert
    v-if="!preview.ok"
    type="error"
    :closable="false"
    :title="preview.error ?? '这份文件无法解析'"
    data-test="preview-error"
  />

  <!-- 题号不存在时后端直接给 ok:false；这条提示不能放在 ok 分支里，否则永远不显示 -->
  <el-alert
    v-if="preview.questions.some((item) => !item.exists)"
    type="error"
    :closable="false"
    class="problems"
    data-test="missing-question"
  >
    <template #title>有些题号还没有评分标准，整份文件都不能导入</template>
    <ul class="problem-list">
      <li v-for="item in preview.questions.filter((entry) => !entry.exists)" :key="item.code">
        {{ item.code }}
      </li>
    </ul>
  </el-alert>

  <template v-if="preview.ok">
    <div class="summary" data-test="preview-summary">
      共 {{ preview.total_rows }} 行；带学号 {{ preview.rows_with_student_id ?? 0 }} 行；
      空行 {{ preview.blank_lines ?? 0 }} 行；重复 {{ duplicateCount }} 行；
      坏行 {{ preview.problems.length }} 行
      <template v-if="preview.question_codes?.length">
        ；涉及题号 {{ preview.question_codes.join('、') }}
        <span v-if="preview.question_codes.length > 1" class="muted">
          （导入后在第二步逐题评分）
        </span>
      </template>
    </div>

    <el-table :data="preview.rows" size="small" data-test="preview-table" max-height="320">
      <el-table-column prop="line" label="行" width="64" />
      <el-table-column prop="question_code" label="题号" width="100" />
      <el-table-column prop="answer_text" label="答案" min-width="280" show-overflow-tooltip />
      <el-table-column label="重复" width="88">
        <template #default="{ row }">
          <el-tag v-if="row.duplicate" type="warning" size="small">重复</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-alert
      v-if="preview.problems.length"
      type="warning"
      :closable="false"
      class="problems"
      data-test="preview-problems"
    >
      <template #title>坏行只报行号与原因，不回显原文</template>
      <ul class="problem-list">
        <li v-for="problem in preview.problems" :key="problem.line">
          第 {{ problem.line }} 行：{{ problem.reason }}
        </li>
      </ul>
    </el-alert>
  </template>
</template>

<style scoped>
.summary {
  margin: 16px 0 8px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 14px;
}

.problems {
  margin-top: 12px;
}

.problem-list {
  margin: 6px 0 0;
  padding-left: 20px;
  line-height: 1.7;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
