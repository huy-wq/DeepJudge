import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import { notifyError } from './feedback'
import { createAppRouter } from './router'
import './styles.css'

const app = createApp(App)

// 最后一道兜底：组件树之外的异常也要留下可读提示，不允许静默白屏
app.config.errorHandler = (error) => {
  console.error(error)
  notifyError(error)
}

app.use(ElementPlus)
app.use(createAppRouter())
app.mount('#app')
