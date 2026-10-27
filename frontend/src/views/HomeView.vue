<script setup lang="ts">
import { useRouter } from 'vue-router'

import { session } from '../session'

const router = useRouter()

const steps = [
  {
    title: '1 评分标准',
    description: '输入题干、满分与一段自然语言标准，生成并逐项确认采分点。',
    to: '/rubrics',
    action: '打开评分标准',
  },
  {
    title: '2 导入与评分',
    description: '导入一个班的答卷，勾选后发起评分；进度、失败项与重试都在同一页。',
    to: '/grading',
    action: '导入答卷',
  },
  {
    title: '3 复核',
    description: '按风险查看需要人工确认的结论，先处理最可疑的那几份。',
    to: '/review',
    action: '打开复核队列',
  },
]
</script>

<template>
  <div class="home">
    <el-card>
      <h2>从一段标准到一份可复核的成绩</h2>
      <p class="lead">
        系统把“让模型直接给总分”拆成逐采分点判定并校验原文证据，分数由服务端计算。
        走完下面三步，每一分都能回到标准与原文。
      </p>

      <div class="steps">
        <div v-for="step in steps" :key="step.to" class="step">
          <h3>{{ step.title }}</h3>
          <p>{{ step.description }}</p>
          <el-button type="primary" plain @click="router.push(step.to)">
            {{ step.action }}
          </el-button>
        </div>
      </div>

      <el-descriptions :column="1" border>
        <el-descriptions-item label="登录状态">
          {{ session.status === 'authenticated' ? '已登录' : '未登录' }}
        </el-descriptions-item>
        <el-descriptions-item label="会话来源">
          服务端 HttpOnly cookie（刷新页面后仍有效，退出即失效）
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<style scoped>
.home {
  max-width: 980px;
}

h2 {
  margin-top: 0;
}

.lead {
  color: var(--el-text-color-regular);
  line-height: 1.7;
}

.steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin: 20px 0;
}

.step {
  padding: 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
}

.step h3 {
  margin: 0 0 8px;
  font-size: 16px;
}

.step p {
  margin: 0 0 12px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
  font-size: 14px;
}

@media (max-width: 880px) {
  .steps {
    grid-template-columns: 1fr;
  }
}
</style>
