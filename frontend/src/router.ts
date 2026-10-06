import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

const LoginView = () => import('./views/LoginView.vue')
const LibraryView = () => import('./views/LibraryView.vue')
const StrategyView = () => import('./views/StrategyView.vue')
const ReportsView = () => import('./views/ReportsView.vue')
const ExecutionView = () => import('./views/ExecutionView.vue')
const AlertsView = () => import('./views/AlertsView.vue')
const SystemView = () => import('./views/SystemView.vue')

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'library', component: LibraryView },
    { path: '/frozen-strategies', name: 'frozen-library', component: LibraryView },
    { path: '/strategies/:id', name: 'strategy', component: StrategyView },
    { path: '/reports', name: 'reports', component: ReportsView },
    { path: '/data', name: 'data', component: () => import('./views/DataView.vue') },
    { path: '/execution', name: 'execution', component: ExecutionView },
    { path: '/alerts', name: 'alerts', component: AlertsView },
    { path: '/system', name: 'system', component: SystemView },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.initialized) await auth.restore()
  if (!to.meta.public && !auth.user) return { name: 'login', query: { next: to.fullPath } }
  if (to.name === 'login' && auth.user) return { name: 'library' }
})

export default router
