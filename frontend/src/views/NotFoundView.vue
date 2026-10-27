<template>
  <el-result icon="warning" title="页面不存在" sub-title="请检查地址，或回到首页继续操作。">
    <template #extra>
      <el-button type="primary" @click="$router.replace('/')">回到首页</el-button>
    </template>
  </el-result>
</template>
