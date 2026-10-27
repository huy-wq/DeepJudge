<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ErrorBoundary from '../components/ErrorBoundary.vue'
import { notifyError } from '../feedback'
import { signOut } from '../session'

const route = useRoute()

const router = useRouter()
const leaving = ref(false)

async function handleLogout(): Promise<void> {
  leaving.value = true
  try {
    await signOut()
    await router.replace({ name: 'login' })
  } catch (error) {
    // 退出失败时不假装已退出：服务端会话可能还在，cookie 也还在
    notifyError(error)
  } finally {
    leaving.value = false
  }
}
</script>

<template>
  <el-container class="admin">
    <el-header class="admin-header">
      <span class="brand">DeepJudge</span>
      <nav class="nav">
        <router-link to="/" data-test="nav-home">首页</router-link>
        <router-link to="/rubrics" data-test="nav-rubrics">评分标准</router-link>
        <router-link to="/grading" data-test="nav-grading">导入与评分</router-link>
        <router-link to="/review" data-test="nav-review">复核</router-link>
      </nav>
      <span class="spacer" />
      <el-button text :loading="leaving" data-test="logout" @click="handleLogout">
        退出
      </el-button>
    </el-header>

    <el-main class="admin-main">
      <!-- key 用 path 而不是 fullPath：查询参数变化（如任务号）不该重置整页状态；
           出错换页面时 path 必然变化，兜底仍然有效 -->
      <ErrorBoundary :key="route.path">
        <router-view />
      </ErrorBoundary>
    </el-main>
  </el-container>
</template>

<style scoped>
.admin {
  min-height: 100vh;
  background: #f6f8fb;
}

.admin-header {
  display: flex;
  align-items: center;
  gap: 24px;
  background: #fff;
  border-bottom: 1px solid var(--el-border-color-light);
}

.brand {
  font-weight: 600;
  font-size: 18px;
  letter-spacing: 0.02em;
}

.nav {
  display: flex;
  gap: 16px;
}

.nav a {
  color: var(--el-text-color-regular);
  text-decoration: none;
}

.nav a.router-link-active {
  color: var(--el-color-primary);
}

.spacer {
  flex: 1;
}

.admin-main {
  padding: 24px;
}
</style>
