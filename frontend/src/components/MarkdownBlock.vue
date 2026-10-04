<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps<{ content: string }>()
marked.setOptions({ gfm: true, breaks: true })
const html = computed(() => DOMPurify.sanitize(marked.parse(props.content) as string))
</script>

<template><div class="markdown" v-html="html" /></template>
