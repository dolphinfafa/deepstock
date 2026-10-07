<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import MarkdownBlock from '../components/MarkdownBlock.vue'

const reports = ref<any[]>([])
const selected = ref<any>(null)
const route = useRoute()
const router = useRouter()

async function open(id: string) {
  selected.value = await api(`/reports/${id}`)
  if (route.query.report !== id) await router.replace({ query: { report: id } })
}
async function load() {
  reports.value = await api('/reports')
  const id = typeof route.query.report === 'string' ? route.query.report : reports.value[0]?.id
  if (id) await open(id)
}
onMounted(load)
watch(() => route.query.report, (id) => { if (typeof id === 'string' && id !== selected.value?.id) open(id) })
</script>

<template>
  <div class="page-wrap"><header class="page-header"><div><div class="eyebrow">RESEARCH ARCHIVE</div><h1>研究报告</h1><p>规范、回测、前向观察和执行复核的只读档案。</p></div></header>
    <div class="report-layout"><aside class="report-list"><button v-for="report in reports" :key="report.id" :class="{ active: report.id === selected?.id }" @click="open(report.id)"><span>{{ report.title }}</span><small>{{ report.as_of_date }} · {{ report.report_type }}</small></button></aside><article class="panel report-reader" v-if="selected"><div class="report-reader-head"><div><small>{{ selected.report_type }}</small><h2>{{ selected.title }}</h2></div><time>{{ selected.as_of_date }}</time></div><section v-for="notice in selected.evidence_notices || []" :key="notice.id" class="form-error"><strong>{{ notice.title }}</strong><p>{{ notice.detail }}</p></section><MarkdownBlock :content="selected.content" /></article></div>
  </div>
</template>
