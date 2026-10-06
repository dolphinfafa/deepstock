<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute(), router = useRouter()
const data = ref<any>(null), detail = ref<any>(null), preview = ref<any>(null), error = ref('')
const market = ref(''), provider = ref(''), node = ref(''), status = ref(''), layer = ref('clean')
const offset = ref(0), rowOffset = ref(0)

async function load() {
  try {
    const query = new URLSearchParams({ offset: String(offset.value), limit: '50' })
    for (const [key, value] of Object.entries({ market: market.value, provider: provider.value, node: node.value, status: status.value })) if (value) query.set(key, value)
    data.value = await api(`/data?${query}`)
    error.value = ''
  } catch (reason: any) { error.value = reason.message }
}
async function choose(id: string) { await router.replace({ query: { version: id } }) }
async function showDetail() {
  if (!route.query.version) { detail.value = null; return }
  try { detail.value = await api(`/data/${route.query.version}`); rowOffset.value = 0; await loadPreview() }
  catch (reason: any) { error.value = reason.message }
}
async function loadPreview() {
  if (!detail.value) return
  try { preview.value = await api(`/data/${detail.value.id}/preview?layer=${layer.value}&offset=${rowOffset.value}&limit=50`); error.value = '' }
  catch (reason: any) { preview.value = null; error.value = reason.message }
}
watch([market, provider, node, status], () => { offset.value = 0; load() })
watch(offset, load)
watch(() => route.query.version, showDetail)
watch(layer, () => { rowOffset.value = 0; loadPreview() })
watch(rowOffset, loadPreview)
onMounted(async () => { await load(); await showDetail() })
</script>

<template>
  <div class="page-wrap">
    <header class="page-header"><div><div class="eyebrow">DATA LINEAGE & QUALITY</div><h1>数据中心</h1><p>原始快照保留证据，清洗版本记录变更，回测入口绑定版本。未知行情不会被插值补造。</p></div><span class="asof">{{ data?.total || 0 }} 个版本</span></header>
    <div v-if="error" class="form-error">{{ error }}</div>
    <section class="toolbar data-filters">
      <select v-model="market"><option value="">全部市场</option><option value="US">美股</option><option value="CN">A股</option><option value="Both">Both</option></select>
      <select v-model="provider"><option value="">全部来源</option><option v-for="p in data?.providers" :key="p">{{ p }}</option></select>
      <select v-model="node"><option value="">全部节点</option><option v-for="n in data?.nodes" :key="n">{{ n }}</option></select>
      <select v-model="status"><option value="">全部质量状态</option><option value="ready">可用</option><option value="blocked">阻塞／隔离</option><option value="evidence">仅原始证据</option></select>
    </section>
    <section class="panel"><div class="table-wrap"><table><thead><tr><th>数据文件 / 版本</th><th>市场 · 来源</th><th>节点</th><th>覆盖区间</th><th>清洗行数</th><th>质量</th></tr></thead><tbody><tr v-for="item in data?.items" :key="item.id" class="data-row" :class="{ selected: detail?.id === item.id }" @click="choose(item.id)"><td><button class="link-button" @click.stop="choose(item.id)">{{ item.source_name }}</button><br /><code>{{ item.id.slice(0, 12) }}</code></td><td>{{ item.market }} · {{ item.provider }}</td><td>{{ item.node }}</td><td>{{ item.data_start || '—' }} → {{ item.data_end || '—' }}</td><td>{{ item.rows.toLocaleString() }}</td><td :class="{ loss: item.status === 'blocked' }">{{ item.status }}<br /><small>{{ item.quality.invalid_rows || 0 }} 异常 · {{ item.quality.missing_sessions || 0 }} 缺口</small></td></tr></tbody></table></div><div class="data-pagination"><button :disabled="offset === 0" @click="offset -= 50">上一页</button><span>{{ offset + 1 }} / {{ data?.total || 0 }}</span><button :disabled="offset + 50 >= (data?.total || 0)" @click="offset += 50">下一页</button></div></section>
    <template v-if="detail">
      <section class="panel"><div class="section-title"><div><small>IMMUTABLE VERSION</small><h2>{{ detail.source_name }}</h2></div><span>{{ detail.status }}</span></div><div class="data-contract"><div><span>版本</span><code>{{ detail.id }}</code></div><div><span>原始层 SHA-256</span><code>{{ detail.raw_sha256 || '—' }}</code></div><div><span>清洗层 SHA-256</span><code>{{ detail.clean_sha256 || '暂无可用清洗文件' }}</code></div><div><span>来源证据</span><strong>{{ detail.origin }}</strong></div><div><span>规则版本</span><strong>{{ detail.rules }}</strong></div><div><span>许可</span><strong>{{ detail.preview_allowed ? '允许本地分页预览' : '仅元信息；行情留在许可节点' }}</strong></div></div><h3>质量报告</h3><ul><li v-for="issue in detail.quality.issues" :key="issue">{{ issue }}</li><li v-if="!detail.quality.issues?.length">已通过当前清洗检查；仍需满足策略入口覆盖要求。</li></ul><p>修改 {{ detail.quality.modified_rows || 0 }} 行 · 去重 {{ detail.quality.duplicate_rows || 0 }} 行 · 隔离 {{ detail.quality.invalid_rows || 0 }} 行 · 补造行情 0 行</p><details><summary>完整契约与质量检查</summary><pre>{{ JSON.stringify({ contract: detail.contract, quality: detail.quality }, null, 2) }}</pre></details><p>关联研究：{{ detail.research_runs.join('、') || '尚无绑定研究运行；旧报告不追认新数据血缘' }}</p></section>
      <section class="panel"><div class="section-title"><div><small>RAW → CLEAN</small><h2>数据预览</h2></div><nav class="market-tabs"><button :class="{ selected: layer === 'raw' }" @click="layer = 'raw'">原始层</button><button :class="{ selected: layer === 'clean' }" @click="layer = 'clean'">清洗层</button></nav></div><p>同一版本切换查看；清洗层 _raw_row 指向原始行，_quality_flags 记录修改。隔离行计入质量报告，不伪装为可用行情。</p><p v-if="preview?.reason">{{ preview.reason }}</p><div v-else class="table-wrap"><table><thead><tr><th v-for="column in preview?.columns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, index) in preview?.rows" :key="index"><td v-for="column in preview?.columns" :key="column">{{ typeof row[column] === 'object' ? JSON.stringify(row[column]) : row[column] }}</td></tr></tbody></table></div><div v-if="preview?.rows?.length" class="data-pagination"><button :disabled="rowOffset === 0" @click="rowOffset -= 50">上50行</button><span>第 {{ rowOffset + 1 }} 行起</span><button :disabled="preview.rows.length < 50" @click="rowOffset += 50">下50行</button></div></section>
    </template>
  </div>
</template>
