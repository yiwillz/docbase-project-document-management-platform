import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/theme-chalk/el-var.css'
import './styles/theme.css'

import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { i18n } from './i18n'
import { router } from './router'

createApp(App).use(createPinia()).use(i18n).use(router).mount('#app')
