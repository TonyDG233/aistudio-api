<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AccountWithStats } from '@/types'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useTagsStore } from '@/stores/tags.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps<{
  modelValue: boolean
  account: AccountWithStats | null
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const accountsStore = useAccountsStore()
const tagsStore = useTagsStore()
const inputName = ref('')
const inputTag = ref('')
const customTags = ref<string[]>([])

const tierTag = computed(() => (props.account?.tier || '').trim().toLowerCase())

watch(
  () => props.account,
  (acc) => {
    if (!acc) return
    inputName.value = acc.name || ''
    customTags.value = [...(acc.tags || [])]
    inputTag.value = ''
    void tagsStore.refresh()
  },
  { immediate: true }
)

function addTag() {
  const parts = inputTag.value.split(/[,，\s]+/).map((part) => part.trim()).filter(Boolean)
  for (const part of parts) {
    const key = part.toLocaleLowerCase()
    const exists = customTags.value.some((tag) => tag.toLocaleLowerCase() === key)
      || key === tierTag.value
    if (!exists) customTags.value.push(part)
  }
  inputTag.value = ''
}

function removeTag(tag: string) {
  customTags.value = customTags.value.filter((item) => item !== tag)
}

async function handleSave() {
  if (!props.account) return
  const trimmed = inputName.value.trim()
  if (!trimmed) return

  await accountsStore.updateAccountProfile(props.account.id, trimmed, customTags.value)
  void tagsStore.refresh()
  emit('update:modelValue', false)
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    title="编辑账号"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
          账号名称 / 备注
        </label>
        <input
          v-model="inputName"
          type="text"
          placeholder="例如: 主账号 (Pro)、备用账号 1 等"
          class="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-sm outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
        >
      </div>

      <div class="space-y-2">
        <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
          账号 tag
        </label>
        <div class="flex flex-wrap items-center gap-1.5">
          <span
            v-if="tierTag"
            class="inline-flex items-center gap-1 rounded-full bg-brand-50 border border-brand-200 px-2 py-1 text-[11px] font-semibold text-brand-700"
            title="tier 自动 tag，不可手动删除"
          >
            {{ tierTag }}
            <span class="text-[9px] font-normal text-brand-500">tier</span>
          </span>
          <span
            v-for="tag in customTags"
            :key="tag"
            class="inline-flex items-center gap-1 rounded-full bg-gray-100 border border-gray-200 px-2 py-1 text-[11px] text-gray-700"
          >
            {{ tag }}
            <button
              type="button"
              class="text-gray-400 hover:text-red-500 transition-colors"
              title="删除 tag"
              @click="removeTag(tag)"
            >
              ×
            </button>
          </span>
          <span
            v-if="!tierTag && !customTags.length"
            class="text-[11px] text-gray-400"
          >
            暂无 tag
          </span>
        </div>
        <div class="flex items-center gap-2">
          <input
            v-model="inputTag"
            type="text"
            placeholder="输入自定义 tag，回车添加"
            class="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
            @keydown.enter.prevent="addTag"
          >
          <Button
            variant="ghost"
            size="sm"
            @click="addTag"
          >
            添加
          </Button>
        </div>
        <p class="text-[11px] text-gray-400">
          tier 由账号档位自动生成，只读；自定义 tag 可自由增删。渠道准入读取这里的 tag。
        </p>
      </div>
    </div>

    <template #footer>
      <Button
        variant="ghost"
        size="sm"
        @click="emit('update:modelValue', false)"
      >
        取消
      </Button>
      <Button
        variant="primary"
        size="sm"
        :disabled="!inputName.trim()"
        @click="handleSave"
      >
        保存修改
      </Button>
    </template>
  </Modal>
</template>
