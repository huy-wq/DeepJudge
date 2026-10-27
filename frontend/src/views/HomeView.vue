<script setup lang="ts">
import { session } from '../session'
</script>

<template>
  <div class="home">
    <el-card>
      <h2>管理端已就绪</h2>
      <p class="lead">
        登录、路由与统一错误提示已经可用；评分标准编辑、答卷导入、评分任务与复核页面
        会在后续工单里接入这里。
      </p>
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
  max-width: 880px;
}

h2 {
  margin-top: 0;
}

.lead {
  color: var(--el-text-color-regular);
  line-height: 1.7;
}
</style>
