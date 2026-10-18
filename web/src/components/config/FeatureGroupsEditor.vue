<script setup lang="ts">
import type { AccountGroupItem } from './types.ts'
import { SURFACE_OPTIONS } from './schema.ts'
import ConfigInput from './ConfigInput.vue'
import { Plus, Trash2, Users } from 'lucide-vue-next'

const props = withDefaults(defineProps<{ groups?: AccountGroupItem[] }>(), {
  groups: () => [],
})

const emit = defineEmits<{
  'update:groups': [val: AccountGroupItem[]]
}>()

function commit(list: AccountGroupItem[]) {
  emit('update:groups', list)
}

function updateGroup(index: number, patch: Partial<AccountGroupItem>) {
  const list = [...props.groups]
  list[index] = { ...list[index], ...patch }
  commit(list)
}

function addGroup() {
  commit([
    ...props.groups,
    {
      name: `group-${props.groups.length + 1}`,
      match_tier: [],
      match_emails_regex: null,
      allowed_surfaces: [],
      max_page_instances: 2,
    },
  ])
}

function removeGroup(index: number) {
  const list = [...props.groups]
  list.splice(index, 1)
  commit(list)
}

function toNumber(val: string | number | null, fallback: number): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : fallback
}

function parseTierList(text: string): string[] {
  const seen: string[] = []
  for (const part of text.split(/[\s,]+/)) {
    const token = part.trim().toLowerCase()
    if (token && !seen.includes(token)) seen.push(token)
  }
  return seen
}

function tierText(group: AccountGroupItem): string {
  return (group.match_tier || []).join(', ')
}

function toggleSurface(index: number, surface: string, checked: boolean) {
  const current = props.groups[index].allowed_surfaces || []
  const next = checked
    ? Array.from(new Set([...current, surface]))
    : current.filter((s) => s !== surface)
  updateGroup(index, { allowed_surfaces: next })
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
        <Users class="w-4 h-4 text-brand-600" />
        <span>账号分组与面编排 (groups)</span>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-brand-50 text-brand-700 hover:bg-brand-100 rounded-lg cursor-pointer"
        @click="addGroup"
      >
        <Plus class="w-3 h-3" />
        <span>添加分组</span>
      </button>
    </div>

    <p class="text-[11px] text-gray-400">
      留空即全部走 tier 默认规则 (free→build、pro/ultra/plus→chat)；超配额对该组请求显式拒绝，不排队不回退。
    </p>

    <div
      v-if="!groups.length"
      class="p-4 text-center text-gray-400 text-xs italic border border-dashed border-gray-200 rounded-xl"
    >
      未配置任何分组，编排器将按 tier 默认规则分发。
    </div>

    <div
      v-for="(group, index) in groups"
      :key="index"
      class="p-3.5 bg-gray-50/70 border border-gray-200/80 rounded-xl space-y-3"
    >
      <div class="flex items-center justify-between">
        <span class="text-[11px] font-mono font-semibold text-gray-500">#{{ index + 1 }}</span>
        <button
          type="button"
          class="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-rose-600 rounded px-1 py-0.5 cursor-pointer"
          @click="removeGroup(index)"
        >
          <Trash2 class="w-3 h-3" />
          <span>删除</span>
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <ConfigInput
          :model-value="group.name ?? ''"
          label="组名"
          placeholder="group"
          @update:model-value="updateGroup(index, { name: $event === null ? '' : String($event) })"
        />
        <ConfigInput
          :model-value="group.max_page_instances ?? 2"
          type="number"
          label="页面实例上限"
          description="0-16"
          placeholder="2"
          @update:model-value="updateGroup(index, { max_page_instances: toNumber($event, 2) })"
        />
        <ConfigInput
          :model-value="tierText(group)"
          label="命中档位 (逗号分隔)"
          description="小写"
          placeholder="pro, ultra, plus"
          @update:model-value="updateGroup(index, { match_tier: parseTierList($event === null ? '' : String($event)) })"
        />
        <ConfigInput
          :model-value="group.match_emails_regex ?? ''"
          label="邮箱正则 (可选)"
          placeholder="留空则不启用"
          @update:model-value="updateGroup(index, { match_emails_regex: $event === null || String($event).trim() === '' ? null : String($event) })"
        />
      </div>

      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-700">
          允许的面 (allowed_surfaces)
        </label>
        <div class="flex flex-wrap items-center gap-3">
          <label
            v-for="opt in SURFACE_OPTIONS"
            :key="opt.value"
            class="inline-flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer"
          >
            <input
              type="checkbox"
              class="rounded border-gray-300 text-brand-600 focus:ring-brand-500/30"
              :checked="(group.allowed_surfaces || []).includes(opt.value)"
              @change="toggleSurface(index, opt.value, ($event.target as HTMLInputElement).checked)"
            >
            <span class="font-mono">{{ opt.label }}</span>
            <span class="text-[11px] text-gray-400">{{ opt.description }}</span>
          </label>
          <span
            v-if="!(group.allowed_surfaces || []).length"
            class="text-[11px] text-rose-500"
          >
            未勾选任何面：该组被禁用，调度层直接排除。
          </span>
        </div>
      </div>
    </div>
  </div>
</template>