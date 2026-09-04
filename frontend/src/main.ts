import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'

// Self-hosted fonts. UI/UX §3.1 makes tabular monospaced figures non-negotiable, and a
// CDN link would make that dependent on network reachability at demo time — these are
// bundled by Vite instead. Weights are limited to the four the UI actually uses.
import '@fontsource-variable/inter/wght.css'
import '@fontsource/jetbrains-mono/latin-400.css'
import '@fontsource/jetbrains-mono/latin-500.css'
import '@fontsource/jetbrains-mono/latin-600.css'

import './styles/tokens.css'
import './styles/base.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
