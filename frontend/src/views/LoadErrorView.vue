<template>
  <div class="load-error">
    <el-result
      icon="error"
      title="页面加载失败"
      sub-title="可能是网络中断，也可能前端刚发布了新版本。刷新一次通常就能恢复。"
    >
      <template #extra>
        <el-button type="primary" @click="reload">刷新页面</el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup lang="ts">
function reload(): void {
  window.location.reload()
}
</script>

<style scoped>
.load-error {
  min-height: 60vh;
  display: grid;
  place-items: center;
}
</style>
