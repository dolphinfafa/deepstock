<script setup lang="ts">
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ metrics: Array<Record<string, any>> }>()
const el = ref<HTMLElement | null>(null)
let chart: ECharts | null = null

function render() {
  if (!el.value) return
  chart ||= init(el.value)
  const rows = props.metrics.filter((metric) => metric.value !== null).slice(0, 8)
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 16, right: 18, top: 14, bottom: 34, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row.name.replaceAll('_', ' ')),
      axisLabel: { color: '#8394a5', rotate: 28, fontSize: 10 },
      axisLine: { lineStyle: { color: '#30404f' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#8394a5' },
      splitLine: { lineStyle: { color: '#22313e' } },
    },
    series: [{
      type: 'bar',
      data: rows.map((row) => ({
        value: row.value,
        itemStyle: { color: row.value < 0 ? '#d76b52' : '#d2a84a', borderRadius: [4, 4, 0, 0] },
      })),
    }],
  })
}

const resize = () => chart?.resize()
onMounted(async () => { await nextTick(); render(); window.addEventListener('resize', resize) })
watch(() => props.metrics, render, { deep: true })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); chart?.dispose() })
</script>

<template><div ref="el" class="metric-chart" /></template>
