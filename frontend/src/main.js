import { createApp } from 'vue'
import './styles/main.css'
import App from './App.vue'

const app = createApp(App)
// 自動聚焦指令（sheet 開啟時的輸入框）
app.directive('focus', {
  mounted(elm) {
    setTimeout(() => {
      elm.focus?.()
      elm.select?.()
    }, 30)
  },
})
app.mount('#app')
