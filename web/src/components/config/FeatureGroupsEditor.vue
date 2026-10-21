<script setup lang="ts">
import { ref } from 'vue'
import type { AccountGroupItem } from './types.ts'
import { SURFACE_OPTIONS } from './schema.ts'
import ConfigInput from './ConfigInput.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { ArrowDown, ArrowUp, ChevronDown, Plus, Trash2, Users } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  groups?: AccountGroupItem[]
  collapsible?: boolean
  defaultOpen?: boolean
}>(), {
  groups: () => [],
  collapsible: true,
  defaultOpen: false,
})

const emit = defineEmits<{
  'update:groups': [val: AccountGroupItem[]]
}>()

const expanded = ref(props.defaultOpen)
const tierDrafts = ref<Record<number, string>>({})

function clearTierDrafts() {
  tierDrafts.value = {}
}

function commit(list: AccountGroupItem[]) {
  emit('update:groups', list)
}

function updateGroup(index: number, patch: Partial<AccountGroupItem>) {
  const list = [...props.groups]
  const next = { ...list[index], ...patch }
  delete next.max_page_instances
  list[index] = next
  commit(list)
}

function nextGroupName(): string {
  const used = new Set(props.groups.map((g) => g.name).filter(Boolean))
  let index = props.groups.length + 1
  while (used.has(`group-${index}`)) index += 1
  return `group-${index}`
}

function addGroup() {
  clearTierDrafts()
  commit([
    ...props.groups,
    {
      name: nextGroupName(),
      enabled: true,
      weight: 1.0,
      match_tier: [],
      match_emails_regex: null,
      allowed_surfaces: [],
      allowed_quota_pools: [],
      max_pages: 2,
    },
  ])
}

function removeGroup(index: number) {
  clearTierDrafts()
  const list = [...props.groups]
  list.splice(index, 1)
  commit(list)
}

function moveGroup(index: number, direction: -1 | 1) {
  const target = index + direction
  if (target < 0 || target >= props.groups.length) return
  clearTierDrafts()
  const list = [...props.groups]
  const [item] = list.splice(index, 1)
  list.splice(target, 0, item)
  commit(list)
}

function toNumber(val: string | number | null, fallback: number): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : fallback
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
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

function tierValue(index: number, group: AccountGroupItem): string {
  return tierDrafts.value[index] ?? tierText(group)
}

function updateTierDraft(index: number, value: string | number | null) {
  const text = value === null ? '' : String(value)
  tierDrafts.value = { ...tierDrafts.value, [index]: text }
  updateGroup(index, { match_tier: parseTierList(text) })
}

function surfacesText(group: AccountGroupItem): string {
  const surfaces = group.allowed_surfaces || []
  if (!surfaces.length) return '未允许任何渠道'
  return surfaces.map((surface) => (surface === 'chat' ? 'PG/Chat' : 'Build')).join(' + ')
}

function toggleSurface(index: number, surface: string, checked: boolean) {
  const current = props.groups[index].allowed_surfaces || []
  const next = checked
    ? Array.from(new Set([...current, surface]))
    : current.filter((s) => s !== surface)
  updateGroup(index, { allowed_surfaces: next })
}

function toggleQuotaPool(index: number, pool: string, checked: boolean) {
  const current = props.groups[index].allowed_quota_pools || []
  const next = checked
    ? Array.from(new Set([...current, pool]))
    : current.filter((p) => p !== pool)
  updateGroup(index, { allowed_quota_pools: next })
}

function isEnabled(group: AccountGroupItem): boolean {
  return group.enabled !== false
}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-2.5">
        <Users class="w-4 h-4 text-brand-600" />
        <div>
          <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
            <span>自定义策略组</span>
            <FieldHelpTip schema-key="features.groups" />
          </h4>
          <p class="text-[11px] text-gray-400 mt-0.5">
            默认按 tier 规则路由；组用于收窄账号、渠道、额度池并调整权重，未配置时不需要关心。
          </p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="collapsible"
          type="button"
          class="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-gray-500 hover:text-gray-900 hover:bg-white rounded-lg cursor-pointer"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >
          <span>{{ expanded ? '收起配置' : '展开配置' }}</span>
          <ChevronDown
            class="w-3.5 h-3.5 transition-transform"
            :class="{ 'rotate-180': expanded }"
          />
        </button>
        <button
          v-if="!collapsible || expanded"
          type="button"
          class="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-brand-50 text-brand-700 hover:bg-brand-100 rounded-lg cursor-pointer"
          @click="addGroup"
        >
          <Plus class="w-3 h-3" />
          <span>添加策略组</span>
        </button>
      </div>
    </div>

    <div
      v-show="!collapsible || expanded"
      class="p-5 space-y-3"
    >
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-400">
        <span>策略组是约束层，不是独立实例池。</span>
        <span>只收窄候选账号/渠道/额度池/权重，不能放宽系统推导的模型与渠道能力。</span>
      </div>

      <div
        v-if="!groups.length"
        class="p-6 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-xl"
      >
        未配置自定义策略组；按 tier 默认规则分发（free→build，pro/ultra/plus→chat）
      </div>

      <div
        v-for="(group, index) in groups"
        :key="index"
        class="p-4 bg-gray-50/70 border border-gray-200/80 rounded-xl space-y-4"
        :class="{ 'opacity-75': !isEnabled(group) }"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex flex-wrap items-center gap-2 min-w-0">
            <span class="text-xs font-mono font-semibold text-gray-600">#{{ index + 1 }}</span>
            <span
              v-if="index === 0"
              class="text-[10px] font-semibold text-brand-700 bg-brand-50 border border-brand-200/70 rounded px-1.5 py-0.5"
            >首个命中生效</span>
            <span
              class="text-[10px] font-semibold rounded px-1.5 py-0.5 border"
              :class="isEnabled(group)
                ? 'text-emerald-700 bg-emerald-50 border-emerald-200'
                : 'text-gray-500 bg-gray-100 border-gray-200'"
            >{{ isEnabled(group) ? '已启用' : '已停用' }}</span>
            <span class="text-[11px] text-gray-400 truncate">
              {{ group.name || '未命名' }} · {{ surfacesText(group) }}
            </span>
          </div>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="p-1 text-gray-400 hover:text-brand-700 hover:bg-white rounded transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
              title="上移"
              :disabled="index === 0"
              @click="moveGroup(index, -1)"
            >
              <ArrowUp class="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              class="p-1 text-gray-400 hover:text-brand-700 hover:bg-white rounded transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
              title="下移"
              :disabled="index === groups.length - 1"
              @click="moveGroup(index, 1)"
            >
              <ArrowDown class="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              class="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-rose-600 rounded px-1.5 py-0.5 cursor-pointer"
              @click="removeGroup(index)"
            >
              <Trash2 class="w-3 h-3" />
              <span>删除</span>
            </button>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ConfigInput
            :model-value="group.name ?? ''"
            label="组名"
            placeholder="group"
            @update:model-value="updateGroup(index, { name: $event === null ? '' : String($event) })"
          />
          <ConfigInput
            :model-value="group.weight ?? 1.0"
            type="number"
            label="权重 (0.1-100)"
            description="命中后参与排序"
            placeholder="1.0"
            @update:model-value="updateGroup(index, { weight: clamp(toNumber($event, 1.0), 0.1, 100) })"
          />
          <ConfigInput
            :model-value="tierValue(index, group)"
            label="命中档位 (逗号分隔)"
            description="小写"
            placeholder="pro, ultra, plus"
            @update:model-value="updateTierDraft(index, $event)"
          />
          <ConfigInput
            :model-value="group.match_emails_regex ?? ''"
            label="邮箱正则 (可选)"
            placeholder="留空则不启用"
            @update:model-value="updateGroup(index, { match_emails_regex: $event === null || String($event).trim() === '' ? null : String($event) })"
          />
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <div class="flex items-center gap-1.5">
              <label class="block text-xs font-semibold text-gray-700">
                允许的渠道 (allowed_surfaces)
              </label>
              <FieldHelpTip
                title="允许的渠道"
                content="组可使用的渠道子集。启用但留空 = 命中该组的账号没有可用渠道，直接拒绝，不会回落。"
                explanation="渠道注册表决定能力，策略组只能在能力范围内收窄；账号标签留空=不限，这里留空=拒绝。"
              />
            </div>
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
              </label>
            </div>
            <p
              v-if="!(group.allowed_surfaces || []).length"
              class="text-[11px] text-amber-600"
            >
              该组已启用但未选渠道：命中即拒绝，不会回落到默认组。
            </p>
          </div>

          <div class="space-y-1.5">
            <div class="flex items-center gap-1.5">
              <label class="block text-xs font-semibold text-gray-700">
                允许的额度池 (allowed_quota_pools)
              </label>
              <FieldHelpTip
                title="允许的额度池"
                content="组可使用的额度池子集：free / pro。留空 = 不限制 (any)。"
                explanation="额度池是可选档位门槛，不是用量/配额，也不是选账号开关；与 allowed_surfaces 留空=拒绝的方向相反。"
              />
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <label
                v-for="pool in ['free', 'pro']"
                :key="pool"
                class="inline-flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer"
              >
                <input
                  type="checkbox"
                  class="rounded border-gray-300 text-brand-600 focus:ring-brand-500/30"
                  :checked="(group.allowed_quota_pools || []).includes(pool)"
                  @change="toggleQuotaPool(index, pool, ($event.target as HTMLInputElement).checked)"
                >
                <span class="font-mono">{{ pool }}</span>
              </label>
              <span class="text-[11px] text-gray-400">留空 = 不限制 (any)</span>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ConfigSwitch
            :model-value="isEnabled(group)"
            label="启用该策略组"
            description="关=停用，视同不存在，继续匹配后续组"
            @update:model-value="updateGroup(index, { enabled: $event })"
          />
        </div>
      </div>
    </div>
  </div>
</template>