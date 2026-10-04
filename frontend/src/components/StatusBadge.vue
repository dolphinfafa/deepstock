<script setup lang="ts">
defineProps<{ status: string }>()

function tone(value: string) {
  if (/完成|done|ok|active|观察|current/i.test(value)) return 'positive'
  if (/阻塞|blocked|失败|error|reject|未通过/i.test(value)) return 'negative'
  if (/research|研究|计划|pending/i.test(value)) return 'neutral'
  return 'warning'
}
</script>

<template><span :class="['status-badge', tone(status)]">{{ status }}</span></template>
