<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { useAuthStore } from '../stores/auth'
import { useLiveStore } from '../stores/live'

const data = ref<any>(null)
const error = ref('')
const message = ref('')
const password = ref('')
const confirmation = ref('')
const auth = useAuthStore()
const live = useLiveStore()

async function load() { try { data.value = await api('/live/overview'); error.value = '' } catch (reason: any) { error.value = reason.message } }
async function setKillSwitch(enabled: boolean) {
  error.value = ''; message.value = ''
  try {
    await api('/execution/settings', { method: 'POST', csrf: auth.csrfToken, body: JSON.stringify({ password: password.value, global_kill_switch: enabled, confirmation: confirmation.value }) })
    message.value = enabled ? '全局实盘总闸已锁定。' : '全局实盘总闸已解锁；策略仍需独立有效授权。'
    password.value = ''; confirmation.value = ''; await load()
  } catch (reason: any) { error.value = reason.message }
}
async function enableLive(enabled: boolean) {
  error.value = ''; message.value = ''
  try {
    await api('/execution/settings', { method: 'POST', csrf: auth.csrfToken, body: JSON.stringify({ password: password.value, live_trading_enabled: enabled, confirmation: confirmation.value }) })
    message.value = enabled ? '有限实盘基础设施已启用。' : '实盘基础设施已关闭。'
    password.value = ''; confirmation.value = ''; await load()
  } catch (reason: any) { error.value = reason.message }
}
onMounted(load)
watch(() => live.revision, load)
</script>

<template>
  <div class="page-wrap"><header class="page-header"><div><div class="eyebrow">EXECUTION CONTROL</div><h1>执行中心</h1><p>账户、持仓、订单和授权。默认拒绝实盘，Paper 与 Live 严格分离。</p></div></header>
    <div v-if="error" class="form-error">{{ error }}</div><div v-if="message" class="form-success">{{ message }}</div>
    <section v-if="data" class="risk-banner" :class="{ unlocked: !data.settings.global_kill_switch }"><div><small>GLOBAL LIVE GATE</small><strong>{{ data.settings.global_kill_switch ? '实盘总闸锁定' : '实盘总闸已解锁' }}</strong></div><div><span>全局上限</span><strong>${{ data.settings.live_notional_cap_usd }}</strong></div><div><span>邮件通知</span><strong>{{ data.settings.email_test_passed ? '已验证' : '未验证' }}</strong></div><div><span>Live 基础设施</span><strong>{{ data.settings.live_trading_enabled ? '开启' : '关闭' }}</strong></div></section>
    <section v-if="data" class="detail-grid"><article class="panel"><div class="section-title"><div><small>ACCOUNT</small><h2>最新账户快照</h2></div><StatusBadge v-if="data.account" :status="data.account.mode" /></div><div v-if="data.account" class="account-grid"><div><span>净资产</span><strong>{{ data.account.currency }} {{ data.account.net_liquidation?.toLocaleString() }}</strong></div><div><span>现金</span><strong>{{ data.account.cash?.toLocaleString() }}</strong></div><div><span>购买力</span><strong>{{ data.account.buying_power?.toLocaleString() }}</strong></div><small>{{ data.account.captured_at?.replace('T', ' ').slice(0, 19) }}</small></div><div v-else class="empty-state">执行节点尚未上报账户快照。</div></article>
      <article class="panel danger-panel"><div class="section-title"><div><small>RISK CONTROLS</small><h2>实盘控制</h2></div></div><p>修改总闸或 Live 状态需要再次输入管理员密码。开启 Live 还要求邮件通知测试成功并输入指定确认语句。</p><label>管理员密码<input v-model="password" type="password" /></label><label>确认语句<input v-model="confirmation" placeholder="ENABLE LIVE TRADING 1000.00" /></label><div class="button-row"><button class="danger-button" @click="setKillSwitch(true)">锁定总闸</button><button class="secondary-button" @click="setKillSwitch(false)">解锁总闸</button><button class="secondary-button" @click="enableLive(false)">关闭 Live</button><button class="primary-button" @click="enableLive(true)">开启 Live</button></div></article></section>
    <section v-if="data" class="panel"><div class="section-title"><div><small>POSITIONS</small><h2>持仓</h2></div></div><div class="table-wrap"><table><thead><tr><th>代码</th><th>数量</th><th>市价</th><th>市值</th><th>平均成本</th></tr></thead><tbody><tr v-for="row in data.positions" :key="row.symbol"><td><strong>{{ row.symbol }}</strong></td><td>{{ row.quantity }}</td><td>{{ row.market_price }}</td><td>{{ row.market_value }}</td><td>{{ row.average_cost }}</td></tr><tr v-if="!data.positions.length"><td colspan="5" class="empty-state">无持仓记录</td></tr></tbody></table></div></section>
    <section v-if="data" class="panel"><div class="section-title"><div><small>ORDERS</small><h2>最近订单</h2></div></div><div class="table-wrap"><table><thead><tr><th>更新时间</th><th>代码</th><th>方向</th><th>数量</th><th>限价</th><th>状态</th></tr></thead><tbody><tr v-for="row in data.orders" :key="row.id"><td>{{ row.updated_at?.slice(0, 19).replace('T', ' ') }}</td><td>{{ row.symbol }}</td><td>{{ row.action }}</td><td>{{ row.quantity }}</td><td>{{ row.limit_price }}</td><td><StatusBadge :status="row.status" /></td></tr><tr v-if="!data.orders.length"><td colspan="6" class="empty-state">尚无订单</td></tr></tbody></table></div></section>
    <section v-if="data" class="panel"><div class="section-title"><div><small>AUTHORIZATIONS</small><h2>策略授权</h2></div></div><div class="table-wrap"><table><thead><tr><th>策略</th><th>模式</th><th>额度</th><th>有效期</th><th>状态</th></tr></thead><tbody><tr v-for="row in data.authorizations" :key="row.id"><td>{{ row.strategy_id }}</td><td>{{ row.mode }}</td><td>${{ row.notional_cap_usd }}</td><td>{{ row.expires_at?.slice(0, 10) }}</td><td><StatusBadge :status="row.status" /></td></tr><tr v-if="!data.authorizations.length"><td colspan="5" class="empty-state">当前没有执行授权</td></tr></tbody></table></div></section>
  </div>
</template>
