<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { useRoute, useRouter } from 'vue-router'
import { markets, marketLabel } from '../markets'
import StatusBadge from '../components/StatusBadge.vue'
import { useLiveStore } from '../stores/live'

const data = ref<any>(null)
const error = ref('')
const query = ref('')
const status = ref('all')
const live = useLiveStore()
const route = useRoute()
const router = useRouter()
const market = computed(() => markets.some((item) => item.value === route.query.market) ? String(route.query.market) : 'all')
const archived = computed(() => route.name === 'frozen-library')

function chooseMarket(value: string) {
  const next = { ...route.query }
  if (value === 'all') delete next.market
  else next.market = value
  router.replace({ query: next })
}

function marketCount(value: string) {
  return (data.value?.strategies || []).filter((row: any) => value === 'all' || row.market === value).length
}

async function load() {
  try { data.value = archived.value ? { strategies: await api('/strategies?archived=true') } : await api('/dashboard'); error.value = '' }
  catch (reason: any) { error.value = reason.message }
}

const strategies = computed(() => (data.value?.strategies || []).filter((row: any) => {
  const text = `${row.display_name} ${row.code} ${row.summary} ${row.market} ${marketLabel(row.market)}`.toLowerCase()
  return text.includes(query.value.toLowerCase()) && (status.value === 'all' || row.status.includes(status.value)) && (market.value === 'all' || row.market === market.value)
}))

function keyMetric(row: any) {
  const metrics = row.latest_run?.metrics || []
  return metrics.find((item: any) => item.value !== null && ['annualized_return', 'oos_total_return', 'total_return', 'baseline_cumulative_return'].includes(item.name)) || metrics[0]
}

function metricText(metric: any) {
  if (!metric) return '待建立基线'
  if (metric.unit === 'ratio') return `${(metric.value * 100).toFixed(1)}%`
  return `${metric.value ?? metric.text_value} ${metric.unit === 'count' ? '项' : ''}`
}

onMounted(load)
watch(() => live.revision, load)
watch(archived, load)
</script>

<template>
  <div class="page-wrap">
    <header class="page-header hero-header">
      <div><div class="eyebrow">{{ archived ? 'FROZEN STRATEGY ARCHIVE' : 'STRATEGY RESEARCH LIBRARY' }}</div><h1>{{ archived ? '冻结策略' : '策略研究库' }}</h1><p>{{ archived ? '暂停推进的策略保留设计、进程和历史报告，恢复研究后再返回首页。' : '所有策略按相同证据标准记录，不以单次回测决定去留。' }}</p></div>
      <div v-if="data?.generated_at" class="asof">页面更新<br /><strong>{{ data.generated_at.slice(0, 10) }}</strong></div>
    </header>
    <div v-if="error" class="form-error">{{ error }}</div>
    <section v-if="data && !archived" class="summary-strip">
      <div><span>推进中策略</span><strong>{{ data.counts.strategies }}</strong></div>
      <div><span>已冻结</span><strong>{{ data.counts.archived }}</strong></div>
      <div><span>影子观察</span><strong>{{ data.counts.shadow }}</strong></div>
      <div><span>Paper 运行</span><strong>{{ data.counts.paper }}</strong></div>
      <div><span>未处理告警</span><strong>{{ data.counts.unacknowledged_alerts }}</strong></div>
      <div class="risk-cell"><span>实盘总闸</span><strong>{{ data.execution.global_kill_switch ? '锁定' : '已解锁' }}</strong></div>
    </section>
    <nav class="market-tabs" aria-label="策略市场分类">
      <button type="button" :class="{ selected: market === 'all' }" :aria-pressed="market === 'all'" @click="chooseMarket('all')">全部 <span>{{ marketCount('all') }}</span></button>
      <button v-for="item in markets" :key="item.value" type="button" :class="{ selected: market === item.value }" :aria-pressed="market === item.value" @click="chooseMarket(item.value)">{{ item.label }} <span>{{ marketCount(item.value) }}</span></button>
    </nav>
    <p v-if="market === 'Both'" class="market-help">Both 只收录明确研究美股与A股的策略，不表示任一市场已通过验证。</p>
    <section class="toolbar">
      <input v-model="query" class="search" placeholder="搜索策略、市场或研究主题…" />
      <select v-model="status"><option value="all">全部阶段</option><option>研究</option><option>观察</option><option>前向</option><option>未通过</option><option>冻结</option></select>
    </section>
    <section class="strategy-grid">
      <RouterLink v-for="row in strategies" :key="row.id" :to="`/strategies/${row.id}`" class="strategy-card">
        <div class="strategy-card-top"><span class="strategy-code">{{ row.code }}</span><StatusBadge :status="row.status" /></div>
        <h2>{{ row.display_name }}</h2>
        <p>{{ row.summary }}</p>
        <p v-if="archived">{{ row.archive_reason }}</p>
        <div class="strategy-meta"><span>{{ marketLabel(row.market) }}</span><span>{{ row.asset_class }}</span><span>{{ row.current_version }}</span></div>
        <div v-if="row.annualization?.scope === 'separate_markets'" class="annualized-slot"><span>成本后年化 · 固定主候选</span><strong v-for="(result, key) in row.market_results" :key="key">{{ marketLabel(String(key)) }} {{ result.symbol }} · {{ (result.metrics.annualized_return * 100).toFixed(2) }}%</strong><small>全历史；两市场分开计算，不合并净值。回溯诊断，非前瞻OOS。</small></div>
        <template v-else><div class="strategy-result"><small>最新关键结果</small><strong>{{ row.latest_run?.status === 'paused_missing_data' ? '需要更多数据' : metricText(keyMetric(row)) }}</strong></div>
        <div class="annualized-slot"><span>年化收益率</span><strong>{{ row.annualization?.value == null ? '暂无' : `${(row.annualization.value * 100).toFixed(2)}%` }}</strong><small>{{ row.annualization?.short_sample ? '短样本参考年化' : row.annualization?.scope || '' }} · {{ row.annualization?.sessions ? `${row.annualization.sessions} 交易日` : row.annualization?.reason }}</small></div></template>
        <p v-if="row.latest_run?.run_type?.startsWith('tail_momentum_')">{{ row.latest_run.summary }}</p>
        <div class="card-foot"><span>{{ row.latest_run?.data_end ? `数据截至 ${row.latest_run.data_end}` : row.latest_run?.as_of_date || '尚无运行日期' }}</span><span>查看完整研究 →</span></div>
      </RouterLink>
    </section>
    <div v-if="data && !strategies.length" class="empty-state">{{ archived ? '暂无冻结策略。' : '没有匹配的策略。' }}</div>
    <section v-if="data?.jobs?.length" class="panel compact-panel">
      <div class="section-title"><div><small>OPERATIONS</small><h2>自动任务</h2></div><RouterLink to="/system">查看系统状态</RouterLink></div>
      <div class="job-row" v-for="job in data.jobs" :key="job.id"><StatusBadge :status="job.status" /><strong>{{ job.display_name }}</strong><span>{{ job.message }}</span><time>{{ job.last_finished_at?.slice(0, 16).replace('T', ' ') }}</time></div>
    </section>
  </div>
</template>
