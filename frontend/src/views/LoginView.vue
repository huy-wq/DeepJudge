<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '../feedback'
import { signIn } from '../session'

const route = useRoute()
const router = useRouter()

const password = ref('')
const error = ref('')
const loading = ref(false)

function safeRedirect(): string {
  const redirect = route.query.redirect
  // 只接受站内路径，避免把 query 里的任意字符串当跳转目标
  if (
    typeof redirect === 'string' &&
    redirect.startsWith('/') &&
    !redirect.startsWith('//') &&
    !redirect.startsWith('/\\')
  ) {
    return redirect
  }
  return '/'
}

async function submit(): Promise<void> {
  if (loading.value) {
    return
  }
  error.value = ''
  if (!password.value) {
    error.value = '请输入管理口令'
    return
  }

  loading.value = true
  try {
    await signIn(password.value)
    await router.replace(safeRedirect())
  } catch (failure) {
    // 表单错误贴在字段旁边；全局提示留给页面/异步错误，避免同一句话出现两次
    error.value = errorMessage(failure)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card">
      <template #header>
        <h1 class="brand">DeepJudge</h1>
        <p class="subtitle">主观试题评判系统 · 管理端</p>
      </template>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="管理口令">
          <el-input
            v-model="password"
            type="password"
            show-password
            placeholder="请输入口令"
            data-test="password"
          />
        </el-form-item>

        <el-alert
          v-if="error"
          :title="error"
          type="error"
          show-icon
          :closable="false"
          class="login-error"
          data-test="error"
        />

        <el-button
          type="primary"
          native-type="submit"
          :loading="loading"
          class="login-submit"
          data-test="submit"
        >
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: linear-gradient(160deg, #f5f7fa 0%, #e4ecf7 100%);
}

.login-card {
  width: min(420px, 100%);
}

.brand {
  margin: 0;
  font-size: 24px;
  letter-spacing: 0.02em;
}

.subtitle {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.login-error {
  margin-bottom: 16px;
}

.login-submit {
  width: 100%;
}
</style>
