<script setup lang="ts">
import { computed, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps<{ content: string; collapsibleTables?: boolean }>()
const container = ref<HTMLElement>()
marked.setOptions({ gfm: true, breaks: true })
const html = computed(() => {
  const clean = DOMPurify.sanitize(marked.parse(props.content) as string)
  if (!props.collapsibleTables) return clean
  const document = new DOMParser().parseFromString(clean, 'text/html')
  document.querySelectorAll('table').forEach((table, index) => {
    const details = document.createElement('details')
    details.className = 'report-table'
    const summary = document.createElement('summary')
    summary.textContent = `表格 ${index + 1} · ${table.querySelectorAll('tbody tr').length} 行 · 展开 / 收起`
    const wrap = document.createElement('div')
    wrap.className = 'table-wrap'
    table.replaceWith(details)
    wrap.append(table)
    details.append(summary, wrap)
  })
  return DOMPurify.sanitize(document.body.innerHTML)
})
const hasTables = computed(() => props.collapsibleTables && html.value.includes('class="report-table"'))
function toggleTables(open: boolean) {
  container.value?.querySelectorAll<HTMLDetailsElement>('details.report-table').forEach((table) => { table.open = open })
}
</script>

<template>
  <div v-if="hasTables" class="report-table-actions"><button class="secondary-button" @click="toggleTables(true)">展开全部表格</button><button class="secondary-button" @click="toggleTables(false)">收起全部表格</button></div>
  <div ref="container" class="markdown" v-html="html" />
</template>
