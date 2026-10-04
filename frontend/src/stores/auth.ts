import { defineStore } from 'pinia'
import { api, ApiError } from '../api'

type User = { username: string; role: string }

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    csrfToken: '',
    initialized: false,
  }),
  actions: {
    async restore() {
      try {
        const payload = await api<{ user: User; csrf_token: string }>('/auth/me')
        this.user = payload.user
        this.csrfToken = payload.csrf_token
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error
        this.user = null
        this.csrfToken = ''
      } finally {
        this.initialized = true
      }
    },
    async login(username: string, password: string) {
      const payload = await api<{ user: User; csrf_token: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      this.user = payload.user
      this.csrfToken = payload.csrf_token
      this.initialized = true
    },
    async logout() {
      await api('/auth/logout', { method: 'POST', csrf: this.csrfToken })
      this.user = null
      this.csrfToken = ''
    },
  },
})
