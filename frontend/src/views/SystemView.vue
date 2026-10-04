<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { useAuthStore } from '../stores/auth'
import { useLiveStore } from '../stores/live'

const dashboard = ref<any>(null)
const liveData = ref<any>(null)
const notes = ref<any[]>([])
const currentPassword = ref('')
const newPassword = ref('')
const message = ref('')
const error = ref('')
const auth = useAuthStore()
const live = useLiveStore()
async function load() {
  const [dashboardResult, liveResult, noteResult] = await Promise.all([
    api<any>('/dashboard'),
    api<any>('/live/overview'),
    api<any[]>('/notes'),
  ])
  dashboard.value = dashboardResult
  liveData.value = liveResult
  notes.value = noteResult
}
async function changePassword() { try { await api('/auth/change-password', { method: 'POST', csrf: auth.csrfToken, body: JSON.stringify({ current_password: currentPassword.value, new_password: newPassword.value }) }); auth.user = null; location.href = `${import.meta.env.BASE_URL}login` } catch (reason: any) { error.value = reason.message } }
async function testWechat() { try { const result: any = await api('/execution/test-wechat', { method: 'POST', csrf: auth.csrfToken }); message.value = result.delivered ? '企业微信测试消息已送达。' : '企业微信未确认送达。'; await load() } catch (reason: any) { error.value = reason.message } }
onMounted(load); watch(() => live.revision, load)
</script>

<template><div class="page-wrap"><header class="page-header"><div><div class="eyebrow">SYSTEM & GOVERNANCE</div><h1>系统状态</h1><p>数据源、自动任务、笔记治理与安全设置。</p></div></header><div v-if="error" class="form-error">{{ error }}</div><div v-if="message" class="form-success">{{ message }}</div>
  <section class="detail-grid"><article class="panel"><div class="section-title"><div><small>JOBS</small><h2>自动任务</h2></div></div><div class="system-row" v-for="row in dashboard?.jobs" :key="row.id"><StatusBadge :status="row.status" /><div><strong>{{ row.display_name }}</strong><small>{{ row.message }}</small></div><time>{{ row.last_finished_at?.slice(0, 19).replace('T', ' ') }}</time></div></article><article class="panel"><div class="section-title"><div><small>DATA SOURCES</small><h2>数据源</h2></div></div><div class="system-row" v-for="row in liveData?.data_sources" :key="row.id"><StatusBadge :status="row.status" /><div><strong>{{ row.provider }}</strong><small>{{ row.node }} · 数据日 {{ row.data_date || '—' }}</small></div><time>{{ row.checked_at?.slice(0, 19).replace('T', ' ') }}</time></div></article></section>
  <section class="panel"><div class="section-title"><div><small>NOTE GOVERNANCE</small><h2>研究笔记评审</h2></div><span>网页只读；决定通过聊天登记</span></div><div v-if="!notes.length" class="empty-state">尚无登记笔记。用户提供的新笔记会先评估，再等待明确决定。</div><div class="note-review" v-for="note in notes" :key="note.id"><div><p>{{ note.original_text }}</p><small>{{ note.received_at?.slice(0, 10) }} · {{ note.scope_type }}<template v-if="note.strategy_id"> · {{ note.strategy_id }}</template></small></div><div><StatusBadge :status="note.status" /><span>建议：{{ note.recommendation }}</span><span>决定：{{ note.user_decision }}</span></div></div></section>
  <section class="detail-grid"><article class="panel"><div class="section-title"><div><small>ALERT CHANNEL</small><h2>企业微信</h2></div></div><p>只有严重执行或系统告警会推送。实盘启用前测试必须成功。</p><button class="secondary-button" @click="testWechat">发送测试告警</button></article><article class="panel"><div class="section-title"><div><small>SECURITY</small><h2>修改密码</h2></div></div><label>当前密码<input v-model="currentPassword" type="password" /></label><label>新密码<input v-model="newPassword" type="password" minlength="8" /></label><button class="primary-button" @click="changePassword">修改并重新登录</button></article></section>
  </div></template>
