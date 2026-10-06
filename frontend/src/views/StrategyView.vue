<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import MarkdownBlock from '../components/MarkdownBlock.vue'
import MetricChart from '../components/MetricChart.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useLiveStore } from '../stores/live'
import { marketLabel } from '../markets'

const route = useRoute()
const live = useLiveStore()
const strategy = ref<any>(null)
const error = ref('')
const metrics = computed(() => strategy.value?.latest_run?.metrics || [])
const separateMarkets = computed(() => strategy.value?.annualization?.scope === 'separate_markets')

function pct(value: number | null | undefined) {
  return value == null ? '—' : `${(value * 100).toFixed(2)}%`
}

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
    <RouterLink class="back-link" :to="{ path: strategy.is_archived ? '/frozen-strategies' : '/', query: { market: strategy.market } }">← {{ strategy.is_archived ? '返回冻结策略' : '返回策略研究库' }} · {{ marketLabel(strategy.market) }}</RouterLink>
    <header class="page-header strategy-heading">
      <span class="strategy-code large-code">{{ strategy.code }}</span>
      <div><div class="eyebrow">{{ marketLabel(strategy.market) }} · {{ strategy.asset_class }}</div><h1>{{ strategy.display_name }}</h1><p>{{ strategy.summary }}</p></div>
      <div class="heading-status"><StatusBadge :status="strategy.status" /><small>{{ strategy.execution_status }}</small></div>
    </header>
    <div class="version-line"><span>当前版本</span><code>{{ strategy.current_version }}</code><span>更新于 {{ strategy.updated_at?.slice(0, 10) }}</span></div>
    <div v-if="strategy.is_archived" class="panel compact-panel"><strong>研究已冻结 · {{ strategy.archived_at?.slice(0, 10) }}</strong><p>{{ strategy.archive_reason }}</p></div>
    <div v-if="strategy.latest_run?.status === 'paused_missing_data'" class="panel compact-panel"><strong>需要更多数据 · 研究已暂停</strong><p>尚无合格真实行情回测结果。补齐分钟历史、交易日历及公司行动数据后，需你明确要求恢复；当前不会自动下载或回测。</p></div>
    <p v-if="strategy.latest_run?.data_end">最新数据截至 {{ strategy.latest_run.data_end }} · 评估日期 {{ strategy.latest_run.as_of_date }}</p>
    <p v-if="strategy.latest_run?.run_type?.startsWith('tail_momentum_')">{{ strategy.latest_run.summary }}</p>
    <section v-if="separateMarkets" class="detail-grid">
      <article v-for="(result, market) in strategy.market_results" :key="market" class="panel">
        <div class="section-title"><div><small>{{ marketLabel(String(market)) }} · {{ result.currency }}</small><h2>{{ result.symbol }} · 独立回测</h2></div></div>
        <p>固定主展示：{{ result.principal_variant }}<template v-if="result.principal_exit_policy"> / {{ result.principal_exit_policy }}</template> · 基础成本 · 报告区间</p>
        <div v-if="result.metrics" class="metric-list">
          <div><span>年化收益率</span><strong>{{ pct(result.metrics.annualized_return) }}</strong></div>
          <div><span>累计收益</span><strong>{{ pct(result.metrics.total_return) }}</strong></div>
          <div><span>最大回撤</span><strong class="loss">{{ pct(result.metrics.maximum_drawdown) }}</strong></div>
          <div><span>Sharpe</span><strong>{{ result.metrics.sharpe_ratio?.toFixed(2) ?? '—' }}</strong></div>
          <div><span>100%买入持有年化 / 回撤</span><strong>{{ pct(result.benchmark_metrics.annualized_return) }} / {{ pct(result.benchmark_metrics.maximum_drawdown) }}</strong></div>
          <div><span>80%初始暴露匹配基准年化</span><strong>{{ pct(result.matched_benchmark_metrics.annualized_return) }}</strong></div>
          <div><span>平均暴露 / 年化换手</span><strong>{{ pct(result.metrics.average_exposure) }} / {{ result.metrics.annualized_turnover.toFixed(2) }}</strong></div>
          <div><span>固定滚动负收益窗口</span><strong>{{ result.diagnostics.negative_windows }} / {{ result.diagnostics.walk_forward_windows }}</strong></div>
        </div>
        <p v-if="result.metrics">{{ result.metrics.start }} — {{ result.metrics.end }} · {{ result.metrics.trading_days }} 交易日</p>
        <p v-else class="form-error">固定主候选未完成：{{ result.diagnostics.blocking_reason }}。不改选其他候选替代。</p>
        <p v-if="result.principal_exit_policy">股票池：{{ result.universe_count }} 只历史成员；最多5仓。{{ result.diagnostics.walk_forward_status === 'insufficient_history_for_504_plus_252_sessions' ? '样本不足504+252日，尚无完整滚动验证窗口。' : '' }}</p>
        <small>单边佣金 {{ result.cost_basis.commission_bps }}bp（最低 {{ result.cost_basis.minimum_commission }} {{ result.currency }}）+滑点 {{ result.cost_basis.slippage_bps }}bp；压力滑点 {{ result.cost_basis.stress_slippage_bps }}bp。现金利息0。<br />{{ result.cost_basis.price_model }}<br />回溯诊断留出不等于前瞻OOS；当前仅研究，不下单。</small>
        <div class="table-wrap"><table><thead><tr><th>全部固定候选</th><th v-if="result.principal_exit_policy">退出规则</th><th>成本</th><th>年化</th><th>回撤</th><th v-if="result.principal_exit_policy">状态</th></tr></thead><tbody><tr v-for="item in result.cases" :key="`${item.variant}-${item.exit_policy || ''}-${item.cost_case}`"><td>{{ item.variant }}</td><td v-if="result.principal_exit_policy">{{ item.exit_policy }}</td><td>{{ item.cost_case }}</td><td>{{ pct(item.periods.full?.annualized_return) }}</td><td>{{ pct(item.periods.full?.maximum_drawdown) }}</td><td v-if="result.principal_exit_policy" :title="item.blocking_reason">{{ item.status === 'blocked' ? item.blocking_reason : '完成' }}</td></tr></tbody></table></div>
        <section v-if="result.optimization">
          <h3>单项优化：同一信号区间不重复开仓</h3>
          <p>只改变重复入场规则，其他参数不变。全部对照保留，主指标仍为原基线；以下是已见历史诊断，不是前瞻OOS。</p>
          <div class="table-wrap"><table><thead><tr><th>入场规则</th><th>成本</th><th>年化</th><th>回撤</th><th>年化换手</th><th>平均暴露</th><th>同区间重复买入</th><th>成本/初始资金</th></tr></thead><tbody>
            <tr v-for="item in result.optimization.cases" :key="`${item.entry_policy}-${item.cost_case}`">
              <td>{{ item.entry_policy === 'signal_level' ? '原基线' : '每区间一次' }}</td><td>{{ item.cost_case }}</td><td>{{ pct(item.periods.full?.annualized_return) }}</td><td>{{ pct(item.periods.full?.maximum_drawdown) }}</td><td>{{ item.periods.full?.annualized_turnover?.toFixed(2) ?? '—' }}</td><td>{{ pct(item.periods.full?.average_exposure) }}</td><td>{{ item.diagnostics?.same_episode_repeat_buys ?? '—' }}</td><td>{{ item.status === 'blocked' ? item.blocking_reason : pct(item.diagnostics?.cash_identity.direct_cost_initial_capital_ratio) }}</td>
            </tr>
          </tbody></table></div>
          <small>成本比是账本金额/初始资金，不是复利收益损失。退出与压力成本归因、分段表现见下方研究报告。</small>
        </section>
      </article>
    </section>
    <section class="panel compact-panel annualized-slot"><span>年化收益率 · {{ strategy.annualization?.scope || '未建立' }}</span><strong>{{ separateMarkets ? '分市场展示' : strategy.annualization?.value == null ? '暂无' : `${(strategy.annualization.value * 100).toFixed(2)}%` }}</strong><p>{{ strategy.annualization?.short_sample ? '短样本参考年化，不作为准入依据。' : '' }} {{ strategy.annualization?.reason }} {{ strategy.annualization?.data_start || '' }} — {{ strategy.annualization?.data_end || '' }}<template v-if="strategy.annualization?.sessions"> · {{ strategy.annualization.sessions }} 交易日</template></p><small>{{ strategy.annualization?.cost_basis }}</small><div v-if="strategy.latest_run?.data_versions?.length">输入版本：<RouterLink v-for="version in strategy.latest_run.data_versions" :key="version" :to="{ path: '/data', query: { version } }"><code>{{ version.slice(0, 12) }}</code> </RouterLink></div></section>
    <section class="detail-grid">
      <article class="panel thesis-panel"><div class="section-title"><div><small>THESIS</small><h2>策略思想</h2></div></div><MarkdownBlock :content="strategy.thesis_md" /></article>
      <article v-if="!separateMarkets" class="panel"><div class="section-title"><div><small>LATEST EVIDENCE</small><h2>最新指标</h2></div></div><div class="metric-list"><div v-for="metric in metrics" :key="`${metric.scope}-${metric.name}`"><span>{{ metric.name.replaceAll('_', ' ') }}</span><strong :class="{ loss: metric.value < 0 }">{{ displayMetric(metric) }}</strong><small>{{ metric.scope }}<template v-if="metric.benchmark"> · {{ metric.benchmark }}</template></small></div></div></article>
    </section>
    <section v-if="!separateMarkets" class="panel chart-panel"><div class="section-title"><div><small>METRIC PROFILE</small><h2>指标轮廓</h2></div><span>保留原始量纲，仅用于快速辨识</span></div><MetricChart :metrics="metrics" /></section>
    <section class="panel"><div class="section-title"><div><small>RESEARCH PROGRESS</small><h2>研究进程</h2></div></div><div class="timeline"><div v-for="item in strategy.progress" :key="item.id" :class="['timeline-item', item.status]"><i /><div><div><strong>{{ item.title }}</strong><StatusBadge :status="item.status" /></div><p>{{ item.detail }}</p><small>{{ item.stage }}</small></div></div></div></section>
    <section class="panel"><div class="section-title"><div><small>RUN HISTORY</small><h2>研究运行</h2></div></div><div class="table-wrap"><table><thead><tr><th>日期</th><th>类型</th><th>状态</th><th>摘要</th><th>指标数</th></tr></thead><tbody><tr v-for="run in strategy.runs" :key="run.id"><td>{{ run.as_of_date || '—' }}</td><td>{{ run.run_type }}</td><td><StatusBadge :status="run.status" /></td><td>{{ run.summary }}</td><td>{{ run.metrics.length }}</td></tr></tbody></table></div></section>
    <section class="detail-grid">
      <article class="panel"><div class="section-title"><div><small>REPORTS</small><h2>研究报告</h2></div></div><RouterLink class="report-row" v-for="report in strategy.reports" :key="report.id" :to="{ path: '/reports', query: { report: report.id } }"><div><strong>{{ report.title }}</strong><small>{{ report.report_type }}</small></div><time>{{ report.as_of_date }}</time></RouterLink></article>
      <article class="panel"><div class="section-title"><div><small>RESEARCH NOTES</small><h2>关联笔记</h2></div></div><div v-if="!strategy.notes.length" class="empty-state">尚无已登记的关联笔记。</div><div class="note-row" v-for="note in strategy.notes" :key="note.id"><p>{{ note.original_text }}</p><div><StatusBadge :status="note.status" /><span>{{ note.recommendation }}</span></div></div></article>
    </section>
  </div>
  <div v-else class="page-wrap"><div v-if="error" class="form-error">{{ error }}</div><div v-else class="loading-state">载入策略研究…</div></div>
</template>
