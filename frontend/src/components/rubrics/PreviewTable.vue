<script setup lang="ts">
import type { RubricData } from '../../api/rubrics'

// 直接改 rubric 里的嵌套字段（对象由编辑页持有），保存时整份提交
defineProps<{ rubric: RubricData }>()
</script>

<template>
  <div class="preview-table" data-test="preview">
    <el-table :data="rubric.items" row-key="id" size="small" border>
      <el-table-column prop="id" label="采分点" width="84" />
      <el-table-column label="描述" min-width="200">
        <template #default="{ row }">
          <el-input v-model="row.desc" size="small" data-test="item-desc" />
        </template>
      </el-table-column>
      <el-table-column label="权重" width="132">
        <template #default="{ row }">
          <el-input-number
            v-model="row.weight"
            :min="0"
            :step="0.5"
            :precision="1"
            size="small"
            controls-position="right"
            data-test="item-weight"
          />
        </template>
      </el-table-column>
      <el-table-column label="等级 / 分值 / 判定依据" min-width="360">
        <template #default="{ row }">
          <div v-for="level in row.levels" :key="level.level" class="level-row">
            <el-tag size="small" type="info" class="level-tag">{{ level.level }}</el-tag>
            <el-input-number
              v-model="level.score"
              :min="0"
              :step="0.5"
              :precision="1"
              size="small"
              controls-position="right"
              class="level-score"
              data-test="level-score"
            />
            <el-input
              v-model="level.criterion"
              size="small"
              placeholder="判定依据"
              data-test="level-criterion"
            />
          </div>
        </template>
      </el-table-column>
    </el-table>

    <h4 class="section-title">扣分项</h4>
    <el-table v-if="rubric.deductions.length" :data="rubric.deductions" size="small" border>
      <el-table-column prop="id" label="编号" width="84" />
      <el-table-column prop="desc" label="说明" />
      <el-table-column prop="penalty" label="扣分" width="96" />
      <el-table-column prop="cap" label="封顶" width="96" />
    </el-table>
    <p v-else class="muted">没有扣分项。</p>
  </div>
</template>

<style scoped>
.level-row {
  display: grid;
  grid-template-columns: 48px 110px 1fr;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.level-tag {
  justify-self: start;
}

.section-title {
  margin: 18px 0 8px;
  font-size: 14px;
}

.muted {
  color: var(--el-text-color-secondary);
  margin: 0;
}
</style>
