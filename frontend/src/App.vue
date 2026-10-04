<script setup lang="ts">
import { RouterView } from 'vue-router'
import AppShell from './components/AppShell.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
</script>

<template>
  <AppShell v-if="auth.user"><RouterView /></AppShell>
  <RouterView v-else />
</template>
