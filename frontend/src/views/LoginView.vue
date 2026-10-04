<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const username = ref('admin')
const password = ref('admin')
const error = ref('')
const loading = ref(false)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

async function submit() {
  loading.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    await router.push(typeof route.query.next === 'string' ? route.query.next : '/')
  } catch (reason: any) {
    error.value = reason.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="login-story">
      <div class="eyebrow">QUANTITATIVE RESEARCH CONTROL PLANE</div>
      <h1>让每一项策略<br />都留下可验证的证据。</h1>
      <p>研究、前瞻观察、模拟执行与风险授权，在同一条审计链上完成。</p>
      <div class="signal-lines"><i /><i /><i /><i /><i /></div>
    </section>
    <section class="login-panel">
      <form class="login-card" @submit.prevent="submit">
        <span class="brand-mark large">DS</span>
        <h2>进入 Deepstock</h2>
        <p>策略研究库与自动化交易控制台</p>
        <label>用户名<input v-model="username" autocomplete="username" /></label>
        <label>密码<input v-model="password" type="password" autocomplete="current-password" /></label>
        <div v-if="error" class="form-error">{{ error }}</div>
        <button class="primary-button" :disabled="loading">{{ loading ? '验证中…' : '登录' }}</button>
        <small>会话使用 HttpOnly 安全 Cookie，所有写操作均需 CSRF 校验。</small>
      </form>
    </section>
  </div>
</template>
