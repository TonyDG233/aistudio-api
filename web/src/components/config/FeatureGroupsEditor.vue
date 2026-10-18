<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { AccountGroupItem } from './types.ts'
import { SURFACE_OPTIONS } from './schema.ts'
import ConfigInput from './ConfigInput.vue'
import { systemApi, type SurfaceInstancesResponse } from '@/api/system.ts'
import { Plus, RefreshCw, Trash2, Users } from 'lucide-vue-next'

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

const instances = ref<SurfaceInstancesResponse | null>(null)
const instancesLoading = ref(false)
const instancesError = ref('')

async function loadInstances() {
  instancesLoading.value = true
  instancesError.value = ''
  try {
    instances.value = await systemApi.getSurfaceInstances()
  } catch (e) {
    instancesError.value = e instanceof Error ? e.message : '获取实例状态失败'
  } finally {
    instancesLoading.value = false
  }
}

onMounted(loadInstances)
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
      留空即走 tier 默认规则（free→build、pro/ultra/plus→chat）；组内同时允许 chat+build 时，非 chat-only 模型优先 build，argon/Pro 系强制 chat；策略/配额拒绝一律显式报错，不排队不回退。
    </p>

    <div class="rounded-xl border border-gray-200/80 bg-white/70 p-3 space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-xs font-semibold text-gray-700">实时实例（单浏览器）</span>
        <button
          type="button"
          class="inline-flex items-center gap-1 text-[11px] text-brand-700 hover:text-brand-800 rounded px-1 py-0.5 cursor-pointer"
          :disabled="instancesLoading"
          @click="loadInstances"
        >
          <RefreshCw
            class="w-3 h-3"
            :class="{ 'animate-spin': instancesLoading }"
          />
          <span>刷新</span>
        </button>
      </div>
      <p class="text-[11px] text-gray-400">
        页面实例 = 浏览器里活着的页面：chat 页与 build 页各计 1。上限按【账号】校验——需要新建 build 页时，若该账号当前存活实例数 ≥ max_page_instances 即显式拒绝（0 = 禁止为该账号新建 build 页；纯 chat 请求不受影响）。
      </p>
      <div
        v-if="instancesError"
        class="text-[11px] text-rose-500"
      >
        {{ instancesError }}
      </div>
      <div
        v-else-if="instances"
        class="text-[11px] font-mono text-gray-600 space-y-0.5"
      >
        <div>
          活跃账号:
          {{ instances.active_account ? (instances.active_account.email || instances.active_account.name || instances.active_account.id) : '无' }}
          <span v-if="instances.active_account && instances.active_account.tier">（{{ instances.active_account.tier }}）</span>
          · 组: {{ instances.group || '-' }}
          · 实例 {{ instances.live_instances }}/{{ instances.effective_max === null ? '-' : instances.effective_max }}
        </div>
        <div>
          chat 页: {{ instances.chat_page.alive ? '存活' : '未挂载' }}
          · build 页: {{ instances.build_page.alive ? (instances.build_page.ready ? '存活·就绪' : '存活·未就绪') : '未挂载' }}
          <span v-if="instances.build_page.account_id">（build 归属账号 {{ instances.build_page.account_id }}）</span>
        </div>
        <div
          v-if="instances.build_page.last_error"
          class="text-rose-500"
        >
          build 页最近错误: {{ instances.build_page.last_error }}
        </div>
        <div
          v-if="instances.retiring_contexts > 0"
          class="text-amber-600"
        >
          切号过渡期旧页（drain 中）: {{ instances.retiring_contexts }}
        </div>
        <div class="text-gray-400">
          注：同一时刻仅活跃账号持有实例；切号过渡期旧页不计入配额。
        </div>
      </div>
    </div>

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