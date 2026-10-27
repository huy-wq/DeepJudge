<script setup lang="ts">
import { computed } from 'vue'

import type { CheckboxValueType } from 'element-plus'

import { issueKey, type RubricIssue } from '../../api/rubrics'

const props = defineProps<{
  issues: RubricIssue[]
  validating: boolean
  error: string | null
  acknowledged: Set<string>
}>()

const emit = defineEmits<{ toggle: [key: string, value: boolean] }>()

const blocking = computed(() => props.issues.filter((issue) => issue.blocking))
const warnings = computed(() => props.issues.filter((issue) => !issue.blocking))

function onToggle(issue: RubricIssue, value: CheckboxValueType): void {
  emit('toggle', issueKey(issue), Boolean(value))
}
</script>

<template>
  <div class="checklist" data-test="checklist">
    <el-alert
      v-if="error"
      type="error"
      :closable="false"
      show-icon
      title="校验失败，下面的清单可能已经过期"
      :description="error"
      data-test="validation-error"
    />
    <p v-else-if="validating" class="muted" data-test="validating">校验中…</p>

    <template v-else>
      <div v-if="!blocking.length && !warnings.length" data-test="checklist-ok">
        <el-tag type="success">没有发现问题</el-tag>
        <span class="ok-text">可以保存；评分前建议再核对一遍预览表。</span>
      </div>

      <div v-if="blocking.length" data-test="blocking-issues">
        <h4 class="group-title blocking-title">
          结构错误（{{ blocking.length }}）· 保存前必须修好
        </h4>
        <el-alert
          v-for="issue in blocking"
          :key="issueKey(issue)"
          type="error"
          :closable="false"
          show-icon
          :title="issue.message"
          :description="issue.code"
          class="issue"
          data-test="blocking-issue"
        />
      </div>

      <div v-if="warnings.length" data-test="warning-issues">
        <h4 class="group-title warning-title">
          提示（{{ warnings.length }}）· 不拦保存，但请逐条确认
        </h4>
        <div v-for="issue in warnings" :key="issueKey(issue)" class="warning-row">
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            :title="issue.message"
            :description="issue.code"
            data-test="warning-issue"
          />
          <el-checkbox
            :model-value="acknowledged.has(issueKey(issue))"
            data-test="acknowledge"
            @update:model-value="onToggle(issue, $event)"
          >
            已知悉
          </el-checkbox>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  margin: 0;
}

.ok-text {
  margin-left: 8px;
  color: var(--el-text-color-regular);
}

.group-title {
  margin: 4px 0 10px;
  font-size: 14px;
}

.blocking-title {
  color: var(--el-color-danger);
}

.warning-title {
  margin-top: 18px;
  color: var(--el-color-warning);
}

.issue {
  margin-bottom: 8px;
}

.warning-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 8px;
}

.warning-row :deep(.el-alert) {
  flex: 1;
}
</style>
