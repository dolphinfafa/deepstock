import { defineStore } from 'pinia'

export const useLiveStore = defineStore('live', {
  state: () => ({ revision: 0, connected: false }),
  actions: {
    bump() {
      this.revision += 1
    },
  },
})
