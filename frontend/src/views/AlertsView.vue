<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { useAuthStore } from '../stores/auth'
import { useLiveStore } from '../stores/live'

const rows = ref<any[]>([])
const auth = useAuthStore()
const live = useLiveStore()
async function load() { rows.value = await api('/alerts') }
async function acknowledge(id: string, value: boolean) { await api(`/alerts/${id}/acknowledge`, { method: 'POST', csrf: auth.csrfToken, body: JSON.stringify({ acknowledged: value }) }); await load() }
onMounted(load); watch(() => live.revision, load)
</script>

<template><div class="page-wrap"><header class="page-header"><div><div class="eyebrow">ALERT CENTER</div><h1>告警</h1><p>研究、数据与执行异常统一记录；严重告警可同时发送邮件。</p></div></header><section class="panel alert-list"><div v-if="!rows.length" class="empty-state">当前没有告警。</div><article v-for="row in rows" :key="row.id" :class="['alert-row', row.severity]"><div class="alert-symbol">{{ row.severity === 'critical' || row.severity === 'error' ? '!' : 'i' }}</div><div><div><StatusBadge :status="row.severity" /><small>{{ row.category }} · {{ row.created_at?.slice(0, 19).replace('T', ' ') }}</small></div><h3>{{ row.title }}</h3><p>{{ row.message }}</p><small>邮件：{{ row.delivered_email ? '已发送' : '未发送' }}</small></div><button class="secondary-button" @click="acknowledge(row.id, !row.acknowledged)">{{ row.acknowledged ? '恢复未处理' : '确认' }}</button></article></section></div></template>
