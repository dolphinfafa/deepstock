<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { eventsUrl } from '../api'
import { useAuthStore } from '../stores/auth'
import { useLiveStore } from '../stores/live'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const live = useLiveStore()
let source: EventSource | null = null

const links = [
  { to: '/', label: '策略研究库', icon: '◫' },
  { to: '/reports', label: '研究报告', icon: '≡' },
  { to: '/execution', label: '执行中心', icon: '↗' },
  { to: '/alerts', label: '告警', icon: '!' },
  { to: '/system', label: '系统状态', icon: '⌁' },
]

async function logout() {
  await auth.logout()
  await router.push('/login')
}

onMounted(() => {
  source = new EventSource(eventsUrl, { withCredentials: true })
  source.addEventListener('open', () => (live.connected = true))
  source.addEventListener('error', () => (live.connected = false))
  source.addEventListener('update', () => live.bump())
})

onBeforeUnmount(() => source?.close())
</script>

<template>
  <div class="app-frame">
    <aside class="sidebar">
      <RouterLink class="brand" to="/">
        <span class="brand-mark">DS</span>
        <span><strong>Deepstock</strong><small>RESEARCH SYSTEM</small></span>
      </RouterLink>
      <nav>
        <RouterLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          :class="{ active: link.to === '/' ? route.path === '/' : route.path.startsWith(link.to) }"
        >
          <span class="nav-icon">{{ link.icon }}</span>{{ link.label }}
        </RouterLink>
      </nav>
      <div class="sidebar-foot">
        <span :class="['connection-dot', { online: live.connected }]" />
        {{ live.connected ? '实时通道已连接' : '实时通道重连中' }}
        <button class="link-button" @click="logout">退出 {{ auth.user?.username }}</button>
      </div>
    </aside>
    <main class="main-panel"><slot /></main>
  </div>
</template>
