<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import MarkdownBlock from '../components/MarkdownBlock.vue'
import MetricChart from '../components/MetricChart.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useLiveStore } from '../stores/live'

const route = useRoute()
const live = useLiveStore()
const strategy = ref<any>(null)
const error = ref('')
const metrics = computed(() => strategy.value?.latest_run?.metrics || [])

async function load() {
  try { strategy.value = await api(`/strategies/${route.params.id}`); error.value = '' }
  catch (reason: any) { error.value = reason.message }
}

function displayMetric(metric: any) {
  if (metric.text_value) return metric.text_value
  if (metric.value === null) return '—'
  if (metric.unit === 'ratio') return `${(metric.value * 100).toFixed(2)}%`
  return Number(metric.value).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
}

onMounted(load)
watch(() => route.params.id, load)
watch(() => live.revision, load)
</script>

<template>
  <div class="page-wrap" v-if="strategy">
    <RouterLink class="back-link" to="/">← 返回策略研究库</RouterLink>
    <header class="page-header strategy-heading">
      <span class="strategy-code large-code">{{ strategy.code }}</span>
      <div><div class="eyebrow">{{ strategy.market }} · {{ strategy.asset_class }}</div><h1>{{ strategy.display_name }}</h1><p>{{ strategy.summary }}</p></div>
      <div class="heading-status"><StatusBadge :status="strategy.status" /><small>{{ strategy.execution_status }}</small></div>
    </header>
    <div class="version-line"><span>当前版本</span><code>{{ strategy.current_version }}</code><span>更新于 {{ strategy.updated_at?.slice(0, 10) }}</span></div>
    <section class="detail-grid">
      <article class="panel thesis-panel"><div class="section-title"><div><small>THESIS</small><h2>策略思想</h2></div></div><MarkdownBlock :content="strategy.thesis_md" /></article>
      <article class="panel"><div class="section-title"><div><small>LATEST EVIDENCE</small><h2>最新指标</h2></div></div><div class="metric-list"><div v-for="metric in metrics" :key="`${metric.scope}-${metric.name}`"><span>{{ metric.name.replaceAll('_', ' ') }}</span><strong :class="{ loss: metric.value < 0 }">{{ displayMetric(metric) }}</strong><small>{{ metric.scope }}<template v-if="metric.benchmark"> · {{ metric.benchmark }}</template></small></div></div></article>
    </section>
    <section class="panel chart-panel"><div class="section-title"><div><small>METRIC PROFILE</small><h2>指标轮廓</h2></div><span>保留原始量纲，仅用于快速辨识</span></div><MetricChart :metrics="metrics" /></section>
    <section class="panel"><div class="section-title"><div><small>RESEARCH PROGRESS</small><h2>研究进程</h2></div></div><div class="timeline"><div v-for="item in strategy.progress" :key="item.id" :class="['timeline-item', item.status]"><i /><div><div><strong>{{ item.title }}</strong><StatusBadge :status="item.status" /></div><p>{{ item.detail }}</p><small>{{ item.stage }}</small></div></div></div></section>
    <section class="panel"><div class="section-title"><div><small>RUN HISTORY</small><h2>研究运行</h2></div></div><div class="table-wrap"><table><thead><tr><th>日期</th><th>类型</th><th>状态</th><th>摘要</th><th>指标数</th></tr></thead><tbody><tr v-for="run in strategy.runs" :key="run.id"><td>{{ run.as_of_date || '—' }}</td><td>{{ run.run_type }}</td><td><StatusBadge :status="run.status" /></td><td>{{ run.summary }}</td><td>{{ run.metrics.length }}</td></tr></tbody></table></div></section>
    <section class="detail-grid">
      <article class="panel"><div class="section-title"><div><small>REPORTS</small><h2>研究报告</h2></div></div><RouterLink class="report-row" v-for="report in strategy.reports" :key="report.id" :to="{ path: '/reports', query: { report: report.id } }"><div><strong>{{ report.title }}</strong><small>{{ report.report_type }}</small></div><time>{{ report.as_of_date }}</time></RouterLink></article>
      <article class="panel"><div class="section-title"><div><small>RESEARCH NOTES</small><h2>关联笔记</h2></div></div><div v-if="!strategy.notes.length" class="empty-state">尚无已登记的关联笔记。</div><div class="note-row" v-for="note in strategy.notes" :key="note.id"><p>{{ note.original_text }}</p><div><StatusBadge :status="note.status" /><span>{{ note.recommendation }}</span></div></div></article>
    </section>
  </div>
  <div v-else class="page-wrap"><div v-if="error" class="form-error">{{ error }}</div><div v-else class="loading-state">载入策略研究…</div></div>
</template>
