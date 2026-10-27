<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'

import { notifyError } from '../feedback'

/**
 * 渲染期兜底：任何子组件抛错都变成一张可读的错误卡片，而不是白屏。
 * 具体页面的接口错误仍由页面自己 catch；这里只兜"没人接住的错"。
 */
const failed = ref(false)
const message = ref('')

onErrorCaptured((error) => {
  message.value = notifyError(error)
  failed.value = true
  // 返回 false：不再向上冒泡，页面仍然可用（导航栏还在，教师可以换页面）
  return false
})

function retry(): void {
  failed.value = false
  message.value = ''
}
</script>

<template>
  <slot v-if="!failed" />
  <el-result v-else icon="error" title="页面出错了" :sub-title="message">
    <template #extra>
      <el-button type="primary" @click="retry">重试</el-button>
    </template>
  </el-result>
</template>
