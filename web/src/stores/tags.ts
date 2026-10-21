import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { accountsApi } from '../api/accounts'

export const useTagsStore = defineStore('tags', () => {
  const tags = ref<string[]>([])
  const loading = ref(false)
  const lastError = ref<string | null>(null)
  const sorted = computed(() => [...tags.value].sort((a, b) => a.localeCompare(b)))

  async function refresh(): Promise<void> {
    loading.value = true
    try {
      tags.value = await accountsApi.listTags()
      lastError.value = null
    } catch (err: unknown) {
      lastError.value = err instanceof Error ? err.message : 'tag 目录读取失败'
    } finally {
      loading.value = false
    }
  }

  return { tags, sorted, loading, lastError, refresh }
})
