<script setup lang="ts">
import { computed } from 'vue'
import type { AccountGroupItem, RoutingConfig, SlotConfigItem } from './types.ts'
import { ArrowRight, GitBranch } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  groups?: AccountGroupItem[]
  slots?: SlotConfigItem[]
  routing?: RoutingConfig
}>(), {
  groups: () => [],
  slots: () => [],
  routing: () => ({}),
})

const flow = ['能力解析', '候选池', '组约束', '排序', '实例落地']

const enabledSlots = computed(() => props.slots.filter((slot) => slot.enabled !== false).length)

function surfacesText(group: AccountGroupItem): string {
  const surfaces = group.allowed_surfaces || []
  if (!surfaces.length) return '无渠道（命中即拒绝）'
  return surfaces.map((surface) => (surface === 'chat' ? 'PG/Chat' : 'Build')).join(' + ')
}

function tiersText(group: AccountGroupItem): string {
  const tiers = group.match_tier || []
  if (tiers.length) return tiers.join(', ')
  return group.match_emails_regex ? '按邮箱匹配' : '未设置（不会命中）'
}

function hasSurfaces(group: AccountGroupItem): boolean {
  return (group.allowed_surfaces || []).length > 0
}

function isEnabled(group: AccountGroupItem): boolean {
  return group.enabled !== false
}

function stateText(group: AccountGroupItem): string {
  if (!isEnabled(group)) return '已停用'
  return hasSurfaces(group) ? '参与编排' : '命中即拒绝'
}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5">
      <GitBranch class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs">
          分组编排摘要（只读）
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          策略组是约束层，不是独立实例池；编辑入口在「策略组」卡，此处只展示当前顺序与落地概览。
        </p>
      </div>
    </div>

    <div class="p-5 space-y-4">
      <div class="flex flex-wrap items-center gap-1.5">
        <template
          v-for="(step, index) in flow"
          :key="step"
        >
          <span class="inline-flex items-center rounded-lg border border-brand-200/70 bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-800">
            {{ index + 1 }}. {{ step }}
          </span>
          <ArrowRight
            v-if="index < flow.length - 1"
            class="w-3.5 h-3.5 text-gray-300"
          />
        </template>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div class="rounded-xl border border-gray-200/80 bg-gray-50/60 px-3.5 py-3">
          <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
            全局页面硬顶
          </div>
          <div class="mt-1 text-base font-bold text-gray-900 font-mono">
            {{ routing.global_max_pages ?? 2 }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-200/80 bg-gray-50/60 px-3.5 py-3">
          <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
            Context 上限
          </div>
          <div class="mt-1 text-base font-bold text-gray-900 font-mono">
            {{ routing.max_contexts ?? 2 }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-200/80 bg-gray-50/60 px-3.5 py-3">
          <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
            启用槽位 / 计划实例
          </div>
          <div class="mt-1 text-base font-bold text-gray-900 font-mono">
            {{ enabledSlots }}
          </div>
        </div>
      </div>

      <div
        v-if="!groups.length"
        class="p-4 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-xl"
      >
        未配置策略组；未命中任何组时按 tier 默认规则兜底（free→build，pro/ultra/plus→PG+Build）。
      </div>

      <div
        v-else
        class="divide-y divide-gray-100 rounded-xl border border-gray-200/80 overflow-hidden"
      >
        <div
          v-for="(group, index) in groups"
          :key="index"
          class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2.5 text-[11px]"
          :class="isEnabled(group) ? (hasSurfaces(group) ? 'bg-white' : 'bg-amber-50/40') : 'bg-gray-50/70 text-gray-400'"
        >
          <span class="font-mono font-semibold text-gray-600">#{{ index + 1 }}</span>
          <span class="font-medium text-gray-800">{{ group.name || '未命名' }}</span>
          <span
            class="rounded px-1.5 py-0.5 text-[10px] font-semibold border"
            :class="!isEnabled(group)
              ? 'text-gray-500 bg-gray-100 border-gray-200'
              : hasSurfaces(group)
                ? 'text-emerald-700 bg-emerald-50 border-emerald-200'
                : 'text-amber-700 bg-amber-50 border-amber-200'"
          >{{ stateText(group) }}</span>
          <span class="text-gray-400">tier: {{ tiersText(group) }}</span>
          <span class="text-gray-400">渠道: {{ surfacesText(group) }}</span>
          <span class="text-gray-400">max_pages: {{ group.max_pages ?? 2 }}</span>
          <span class="text-gray-400">weight: {{ group.weight ?? 1.0 }}</span>
        </div>
      </div>

      <p class="text-[11px] text-gray-400 leading-relaxed">
        未命中任何策略组时按 tier 默认规则兜底；停用组视同不存在，启用但未选渠道的组=命中即拒绝，不会回落。槽位只做预留，不绕过渠道、策略组与全局预算。
      </p>
    </div>
  </div>
</template>