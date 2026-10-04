<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { useLiveStore } from '../stores/live'

const data = ref<any>(null)
const error = ref('')
const query = ref('')
const status = ref('all')
const live = useLiveStore()

async function load() {
  try { data.value = await api('/dashboard'); error.value = '' }
  catch (reason: any) { error.value = reason.message }
}

const strategies = computed(() => (data.value?.strategies || []).filter((row: any) => {
  const text = `${row.display_name} ${row.code} ${row.summary} ${row.market}`.toLowerCase()
  return text.includes(query.value.toLowerCase()) && (status.value === 'all' || row.status.includes(status.value))
}))

function keyMetric(row: any) {
  const metrics = row.latest_run?.metrics || []
  return metrics.find((item: any) => ['annualized_return', 'oos_total_return', 'total_return', 'baseline_cumulative_return'].includes(item.name)) || metrics[0]
}

function metricText(metric: any) {
  if (!metric) return '待建立基线'
  if (metric.unit === 'ratio') return `${(metric.value * 100).toFixed(1)}%`
  return `${metric.value ?? metric.text_value} ${metric.unit === 'count' ? '项' : ''}`
}

onMounted(load)
watch(() => live.revision, load)
</script>

<template>
  <div class="page-wrap">
    <header class="page-header hero-header">
      <div><div class="eyebrow">STRATEGY RESEARCH LIBRARY</div><h1>策略研究库</h1><p>所有策略按相同证据标准记录，不以单次回测决定去留。</p></div>
      <div class="asof">研究快照<br /><strong>2026.10.04</strong></div>
    </header>
    <div v-if="error" class="form-error">{{ error }}</div>
    <section v-if="data" class="summary-strip">
      <div><span>已登记策略</span><strong>{{ data.counts.strategies }}</strong></div>
      <div><span>影子观察</span><strong>{{ data.counts.shadow }}</strong></div>
      <div><span>Paper 运行</span><strong>{{ data.counts.paper }}</strong></div>
      <div><span>未处理告警</span><strong>{{ data.counts.unacknowledged_alerts }}</strong></div>
      <div class="risk-cell"><span>实盘总闸</span><strong>{{ data.execution.global_kill_switch ? '锁定' : '已解锁' }}</strong></div>
    </section>
    <section class="toolbar">
      <input v-model="query" class="search" placeholder="搜索策略、市场或研究主题…" />
      <select v-model="status"><option value="all">全部阶段</option><option>研究</option><option>观察</option><option>前向</option><option>未通过</option></select>
    </section>
    <section class="strategy-grid">
      <RouterLink v-for="row in strategies" :key="row.id" :to="`/strategies/${row.id}`" class="strategy-card">
        <div class="strategy-card-top"><span class="strategy-code">{{ row.code }}</span><StatusBadge :status="row.status" /></div>
        <h2>{{ row.display_name }}</h2>
        <p>{{ row.summary }}</p>
        <div class="strategy-meta"><span>{{ row.market }}</span><span>{{ row.asset_class }}</span><span>{{ row.current_version }}</span></div>
        <div class="strategy-result"><small>最新关键结果</small><strong>{{ metricText(keyMetric(row)) }}</strong></div>
        <div class="card-foot"><span>{{ row.latest_run?.as_of_date || '尚无运行日期' }}</span><span>查看完整研究 →</span></div>
      </RouterLink>
    </section>
    <section v-if="data?.jobs?.length" class="panel compact-panel">
      <div class="section-title"><div><small>OPERATIONS</small><h2>自动任务</h2></div><RouterLink to="/system">查看系统状态</RouterLink></div>
      <div class="job-row" v-for="job in data.jobs" :key="job.id"><StatusBadge :status="job.status" /><strong>{{ job.display_name }}</strong><span>{{ job.message }}</span><time>{{ job.last_finished_at?.slice(0, 16).replace('T', ' ') }}</time></div>
    </section>
  </div>
</template>
